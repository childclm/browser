import socket
import ssl
import sys
import ctypes
import math
import threading
import time
import urllib.parse

import dukpy
import sdl2
import skia

# --- 1. 网络请求模块 ---
COOKIE_JAR = {}

NAMED_COLORS = {
    "black": "#000000",
    "gray": "#808080",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#00ff00",
    "blue": "#0000ff",
    "lightblue": "#add8e6",
    "lightgreen": "#90ee90",
    "orange": "#ffa500",
    "orangered": "#ff4500",
}

def parse_color(color):
    if color.startswith("#") and len(color) == 7:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return skia.Color(r, g, b)
    elif color.startswith("#") and len(color) == 9:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        a = int(color[7:9], 16)
        return skia.Color(r, g, b, a)
    elif color in NAMED_COLORS:
        return parse_color(NAMED_COLORS[color])
    else:
        return skia.ColorBLACK

def parse_blend_mode(blend_mode_str):
    if blend_mode_str == "multiply":
        return skia.BlendMode.kMultiply
    elif blend_mode_str == "difference":
        return skia.BlendMode.kDifference
    elif blend_mode_str == "destination-in":
        return skia.BlendMode.kDstIn
    elif blend_mode_str == "source-over":
        return skia.BlendMode.kSrcOver
    else:
        return skia.BlendMode.kSrcOver

def parse_px(value, default=0.0):
    if value is None:
        return default
    try:
        if value.endswith("px"):
            return float(value[:-2])
        return float(value)
    except Exception:
        return default


class Task:
    def __init__(self, task_code, *args):
        self.task_code = task_code
        self.args = args

    def run(self):
        self.task_code(*self.args)
        self.task_code = None
        self.args = None


class TaskRunner:
    def __init__(self, tab):
        self.tab = tab
        self.condition = threading.Condition()
        self.tasks = []
        self.needs_quit = False

    def schedule_task(self, task):
        with self.condition:
            self.tasks.append(task)
            self.condition.notify_all()

    def clear_pending_tasks(self):
        with self.condition:
            self.tasks.clear()

    def set_needs_quit(self):
        with self.condition:
            self.needs_quit = True
            self.condition.notify_all()

    def run_one(self):
        with self.condition:
            if self.needs_quit or len(self.tasks) == 0:
                return False
            task = self.tasks.pop(0)
        task.run()
        return True

    def run_tasks(self):
        count = 0
        while self.run_one():
            count += 1
        return count


REFRESH_RATE_SEC = 0.033


class URL:
    def __init__(self, url):
        # 防止空字符串
        if not url or url.strip() == "":
            url = "https://browser.engineering/"
        url = url.strip()

        if url.startswith("about:"):
            self.scheme = "about"
            self.host = ""
            self.port = None
            self.path = url[len("about:"):]
            return
        
        # 如果没有协议前缀，自动添加协议
        if not url.startswith("http://") and not url.startswith("https://"):
            # 强制 HTTPS 的域名列表
            https_domains = [
                "github.com", "www.github.com", 
                "google.com", "www.google.com",
                "youtube.com", "www.youtube.com",
                "twitter.com", "www.twitter.com",
                "facebook.com", "www.facebook.com",
                "stackoverflow.com", "www.stackoverflow.com",
                "gitlab.com", "www.gitlab.com",
                "browser.engineering", "www.browser.engineering"
            ]
            # 检查是否匹配强制 HTTPS 的域名
            force_https = False
            for domain in https_domains:
                if url.startswith(domain) or url.startswith(domain + "/"):
                    force_https = True
                    break
            if force_https:
                url = "https://" + url
            else:
                url = "http://" + url
        
        self.scheme, url = url.split("://", 1)
        assert self.scheme in ["http", "https"]

        if self.scheme == "http":
            self.port = 80
        elif self.scheme == "https":
            self.port = 443

        if "/" not in url:
            url = url + "/"
        self.host, url = url.split("/", 1)
        self.path = "/" + url

        if ":" in self.host:
            self.host, port = self.host.split(":", 1)
            self.port = int(port)

    def origin(self):
        if self.scheme == "about":
            return "about:"
        return self.scheme + "://" + self.host + ":" + str(self.port)

    def request(self, referrer=None, payload=None):
        if self.scheme == "about":
            return {}, ""
        try:
            s = socket.socket(
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            s.connect((self.host, self.port))

            if self.scheme == "https":
                ctx = ssl.create_default_context()
                s = ctx.wrap_socket(s, server_hostname=self.host)

            method = "POST" if payload is not None else "GET"
            request = f"{method} {self.path} HTTP/1.0\r\n"
            request += f"Host: {self.host}\r\n"
            if self.host in COOKIE_JAR:
                cookie, params = COOKIE_JAR[self.host]
                allow_cookie = True
                if referrer and params.get("samesite", "none") == "lax":
                    if method != "GET":
                        allow_cookie = self.host == referrer.host
                if allow_cookie:
                    request += f"Cookie: {cookie}\r\n"
            if payload is not None:
                length = len(payload.encode("utf-8"))
                request += f"Content-Length: {length}\r\n"
                request += "Content-Type: application/x-www-form-urlencoded\r\n"
            request += "\r\n"
            if payload is not None:
                request += payload
            s.send(request.encode("utf-8"))

            response = s.makefile("r", encoding="utf-8", newline="\r\n")

            statusline = response.readline()
            if not statusline:
                print(f"DEBUG: Empty response from {self.host}")
                return {}, ""
            
            version, status, explanation = statusline.split(" ", 2)
            
            # 处理重定向 (3xx)
            if status.startswith("3"):
                print(f"DEBUG: Redirect {status} to {explanation}")
                response_headers = {}
                while True:
                    line = response.readline()
                    if line == "\r\n":
                        break
                    header, value = line.split(":", 1)
                    response_headers[header.casefold()] = value.strip()
                if "location" in response_headers:
                    location = response_headers["location"]
                    print(f"DEBUG: Following redirect to {location}")
                    new_url = self.resolve(location)
                    return new_url.request(referrer)
                return response_headers, ""

            response_headers = {}
            while True:
                line = response.readline()
                if line == "\r\n":
                    break
                header, value = line.split(":", 1)
                response_headers[header.casefold()] = value.strip()

            assert "transfer-encoding" not in response_headers
            assert "content-encoding" not in response_headers

            if "set-cookie" in response_headers:
                cookie = response_headers["set-cookie"]
                params = {}
                if ";" in cookie:
                    cookie, rest = cookie.split(";", 1)
                    for param in rest.split(";"):
                        if "=" in param:
                            param, value = param.split("=", 1)
                        else:
                            value = "true"
                        params[param.strip().casefold()] = value.casefold()
                COOKIE_JAR[self.host] = (cookie, params)

            body = response.read()
            s.close()
            return response_headers, body
        except Exception as e:
            print(f"DEBUG: Request error: {e}")
            return {}, ""

    def resolve(self, url):
        if not url or url.strip() == "":
            return URL("https://browser.engineering/")
        
        if "://" in url:
            return URL(url)
        if url.startswith("//"):
            return URL(self.scheme + ":" + url)
        if not url.startswith("/"):
            dir, _ = self.path.rsplit("/", 1)
            while url.startswith("../"):
                _, url = url.split("/", 1)
                if "/" in dir:
                    dir, _ = dir.rsplit("/", 1)
            url = dir + "/" + url
        return URL(self.scheme + "://" + self.host + ":" + str(self.port) + url)

    def __str__(self):
        if self.scheme == "about":
            return "about:" + self.path
        port_part = ":" + str(self.port)
        if self.scheme == "https" and self.port == 443:
            port_part = ""
        if self.scheme == "http" and self.port == 80:
            port_part = ""
        return self.scheme + "://" + self.host + port_part + self.path

# --- 2. HTML 解析模块 ---
class Text:
    def __init__(self, text, parent):
        self.text = text
        self.children = []
        self.parent = parent
        self.style = {}
        self.animations = {}
        self.is_focused = False
        self.layout_objects = []

    def __repr__(self):
        return repr(self.text)

class Element:
    def __init__(self, tag, attributes, parent):
        self.tag = tag
        self.attributes = attributes
        self.children = []
        self.parent = parent
        self.style = {}
        self.animations = {}
        self.is_focused = False
        self.layout_objects = []

    def __repr__(self):
        return "<" + self.tag + ">"

class HTMLParser:
    SELF_CLOSING_TAGS = [
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ]
    HEAD_TAGS = [
        "base", "basefont", "bgsound", "noscript",
        "link", "meta", "title", "style", "script",
    ]

    def __init__(self, body):
        self.body = body
        self.unfinished = []

    def parse(self):
        text = ""
        in_tag = False
        for c in self.body:
            if c == "<":
                in_tag = True
                if text:
                    self.add_text(text)
                text = ""
            elif c == ">":
                in_tag = False
                self.add_tag(text)
                text = ""
            else:
                text += c
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def get_attributes(self, text):
        text = text.strip()
        i = 0
        while i < len(text) and not text[i].isspace():
            i += 1
        tag = text[:i].casefold()
        attributes = {}
        while i < len(text):
            while i < len(text) and text[i].isspace():
                i += 1
            if i >= len(text):
                break
            start = i
            while i < len(text) and not text[i].isspace() and text[i] != "=":
                i += 1
            key = text[start:i].casefold()
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "=":
                i += 1
                while i < len(text) and text[i].isspace():
                    i += 1
                if i < len(text) and text[i] in ["'", "\""]:
                    quote = text[i]
                    i += 1
                    start = i
                    while i < len(text) and text[i] != quote:
                        i += 1
                    value = text[start:i]
                    i += 1
                else:
                    start = i
                    while i < len(text) and not text[i].isspace():
                        i += 1
                    value = text[start:i]
                attributes[key] = value
            else:
                attributes[key] = ""
        return tag, attributes

    def add_text(self, text):
        if text.isspace():
            return
        self.implicit_tags(None)
        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        tag, attributes = self.get_attributes(tag)
        if tag.startswith("!"):
            return
        self.implicit_tags(tag)

        if tag.startswith("/"):
            if len(self.unfinished) == 1:
                return
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            parent = self.unfinished[-1] if self.unfinished else None
            node = Element(tag, attributes, parent)
            self.unfinished.append(node)

    def implicit_tags(self, tag):
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                self.add_tag("html")
            elif open_tags == ["html"] and tag not in ["head", "body", "/html"]:
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif open_tags == ["html", "head"] and tag not in ["/head"] + self.HEAD_TAGS:
                self.add_tag("/head")
            else:
                break

    def finish(self):
        if not self.unfinished:
            self.implicit_tags(None)
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        return self.unfinished.pop()

def tree_to_list(tree, list):
    list.append(tree)
    for child in tree.children:
        tree_to_list(child, list)
    return list

# --- 3. CSS 解析模块 ---
class CSSParser:
    def __init__(self, s):
        self.s = s
        self.i = 0

    def whitespace(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def literal(self, literal):
        if not (self.i < len(self.s) and self.s[self.i] == literal):
            raise Exception("Parsing error")
        self.i += 1

    def word(self):
        start = self.i
        in_quote = False
        while self.i < len(self.s):
            cur = self.s[self.i]
            if cur in ["'", '"']:
                in_quote = not in_quote
            if cur.isalnum() or cur in ",/#-.%()\"'" or (
                in_quote and cur == ":"
            ):
                self.i += 1
            else:
                break
        if not (self.i > start):
            raise Exception("Parsing error")
        return self.s[start:self.i]

    def ignore_until(self, chars):
        while self.i < len(self.s):
            if self.s[self.i] in chars:
                return self.s[self.i]
            else:
                self.i += 1
        return None

    def until_chars(self, chars):
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in chars:
            self.i += 1
        return self.s[start:self.i]

    def pair(self, until=[";", "}"]):
        prop = self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()
        val = self.until_chars(until)
        return prop.casefold(), val.strip()

    def body(self):
        pairs = {}
        while self.i < len(self.s) and self.s[self.i] != "}":
            try:
                prop, val = self.pair()
                pairs[prop] = val
                self.whitespace()
                self.literal(";")
                self.whitespace()
            except Exception:
                why = self.ignore_until([";", "}"])
                if why == ";":
                    self.literal(";")
                    self.whitespace()
                else:
                    break
        return pairs

    def selector(self):
        out = self.simple_selector()
        self.whitespace()
        while self.i < len(self.s) and self.s[self.i] != "{":
            descendant = self.simple_selector()
            out = DescendantSelector(out, descendant)
            self.whitespace()
        return out

    def simple_selector(self):
        out = TagSelector(self.word().casefold())
        if self.i < len(self.s) and self.s[self.i] == ":":
            self.literal(":")
            pseudoclass = self.word().casefold()
            out = PseudoclassSelector(pseudoclass, out)
        return out

    def media_query(self):
        self.literal("@")
        prop = self.word().casefold()
        self.whitespace()
        value = self.until_chars(["{"]).strip()
        return prop, value

    def parse(self):
        rules = []
        while self.i < len(self.s):
            try:
                self.whitespace()
                media = None
                if self.i < len(self.s) and self.s[self.i] == "@":
                    prop, value = self.media_query()
                    if prop == "media" and "dark" in value.casefold():
                        media = "dark"
                    elif prop == "media" and "light" in value.casefold():
                        media = "light"
                    self.literal("{")
                    self.whitespace()

                selector = self.selector()
                self.literal("{")
                self.whitespace()
                body = self.body()
                self.literal("}")
                rules.append((media, selector, body))

                if media:
                    self.whitespace()
                    self.literal("}")
                    self.whitespace()
            except Exception:
                why = self.ignore_until(["}"])
                if why == "}":
                    self.literal("}")
                    self.whitespace()
                else:
                    break
        return rules

# --- 4. 选择器模块 ---
class TagSelector:
    def __init__(self, tag):
        self.tag = tag
        self.priority = 1

    def matches(self, node):
        return isinstance(node, Element) and self.tag == node.tag

class DescendantSelector:
    def __init__(self, ancestor, descendant):
        self.ancestor = ancestor
        self.descendant = descendant
        self.priority = ancestor.priority + descendant.priority

    def matches(self, node):
        if not self.descendant.matches(node):
            return False
        while node.parent:
            if self.ancestor.matches(node.parent):
                return True
            node = node.parent
        return False


class PseudoclassSelector:
    def __init__(self, pseudoclass, base):
        self.pseudoclass = pseudoclass
        self.base = base
        self.priority = base.priority

    def matches(self, node):
        return self.base.matches(node) and (
            self.pseudoclass == "focus" and node.is_focused
        )

# --- 5. 样式应用与级联 ---
INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "color": "black",
}

def parse_transition(value):
    transitions = {}
    if not value:
        return transitions
    for item in value.split(","):
        parts = item.strip().split()
        if len(parts) < 2:
            continue
        property_name, duration = parts[0].casefold(), parts[1].casefold()
        try:
            if duration.endswith("ms"):
                seconds = float(duration[:-2]) / 1000.0
            elif duration.endswith("s"):
                seconds = float(duration[:-1])
            else:
                continue
            transitions[property_name] = max(
                1, int(math.ceil(seconds / REFRESH_RATE_SEC))
            )
        except ValueError:
            continue
    return transitions


def parse_transform(transform_str):
    if not transform_str or "translate(" not in transform_str:
        return None
    try:
        start = transform_str.index("translate(") + len("translate(")
        end = transform_str.index(")", start)
        args = transform_str[start:end].replace(",", " ").split()
        if len(args) == 1:
            args.append("0px")
        if len(args) < 2:
            return None
        return parse_px(args[0]), parse_px(args[1])
    except (ValueError, IndexError):
        return None


def map_translation(rect, translation, reversed=False):
    if not translation:
        return rect
    x, y = translation
    matrix = skia.Matrix()
    matrix.setTranslate(-x if reversed else x, -y if reversed else y)
    return matrix.mapRect(rect)


def absolute_bounds_for_obj(obj):
    if obj.x is None or obj.y is None:
        return skia.Rect.MakeEmpty()
    rect = skia.Rect.MakeXYWH(obj.x, obj.y, obj.width, obj.height)
    node = obj.node
    while node:
        rect = map_translation(
            rect, parse_transform(node.style.get("transform", ""))
        )
        node = node.parent
    return rect


class NumericAnimation:
    def __init__(self, old_value, new_value, num_frames):
        self.old_value = float(old_value)
        self.new_value = float(new_value)
        self.num_frames = max(1, num_frames)
        self.frame_count = 0
        self.done = False

    def animate(self):
        self.frame_count += 1
        progress = min(1.0, self.frame_count / self.num_frames)
        if progress >= 1.0:
            self.done = True
        value = self.old_value + (self.new_value - self.old_value) * progress
        return str(value)


class TransformAnimation:
    def __init__(self, old_value, new_value, num_frames):
        self.old_value = old_value or (0.0, 0.0)
        self.new_value = new_value or (0.0, 0.0)
        self.num_frames = max(1, num_frames)
        self.frame_count = 0
        self.done = False

    def animate(self):
        self.frame_count += 1
        progress = min(1.0, self.frame_count / self.num_frames)
        if progress >= 1.0:
            self.done = True
        x = self.old_value[0] + (
            self.new_value[0] - self.old_value[0]
        ) * progress
        y = self.old_value[1] + (
            self.new_value[1] - self.old_value[1]
        ) * progress
        return "translate({}px, {}px)".format(x, y)


def diff_styles(old_style, new_style):
    transitions = parse_transition(new_style.get("transition"))
    changed = {}
    for property_name, num_frames in transitions.items():
        if property_name == "all":
            candidates = ["opacity", "transform"]
        else:
            candidates = [property_name]
        for candidate in candidates:
            if candidate not in old_style or candidate not in new_style:
                continue
            old_value = old_style[candidate]
            new_value = new_style[candidate]
            if old_value == new_value:
                continue
            if candidate == "opacity":
                try:
                    changed[candidate] = (
                        old_value, new_value, num_frames,
                        NumericAnimation(
                            old_value, new_value, num_frames
                        ),
                    )
                except ValueError:
                    pass
            elif candidate == "transform":
                old_transform = parse_transform(old_value)
                new_transform = parse_transform(new_value)
                if old_transform != new_transform:
                    changed[candidate] = (
                        old_value, new_value, num_frames,
                        TransformAnimation(
                            old_transform, new_transform, num_frames
                        ),
                    )
    return changed


def style(node, rules, tab=None):
    old_style = node.style
    node.style = {}
    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property] = node.parent.style[property]
        else:
            if property == "color" and tab and tab.dark_mode:
                node.style[property] = "white"
            else:
                node.style[property] = default_value

    for rule in rules:
        if len(rule) == 3:
            media, selector, body = rule
            if media and tab:
                if (media == "dark") != tab.dark_mode:
                    continue
        else:
            selector, body = rule
        if not selector.matches(node):
            continue
        for property, value in body.items():
            node.style[property] = value

    if isinstance(node, Element) and "style" in node.attributes:
        pairs = CSSParser(node.attributes["style"]).body()
        for property, value in pairs.items():
            node.style[property] = value

    if node.style["font-size"].endswith("%"):
        if node.parent:
            parent_font_size = node.parent.style["font-size"]
        else:
            parent_font_size = INHERITED_PROPERTIES["font-size"]
        node_pct = float(node.style["font-size"][:-1]) / 100
        parent_px = float(parent_font_size[:-2])
        node.style["font-size"] = str(node_pct * parent_px) + "px"

    if old_style:
        for property_name, (
            old_value, new_value, _num_frames, animation
        ) in diff_styles(old_style, node.style).items():
            node.animations[property_name] = animation
            node.style[property_name] = animation.animate()
            if tab:
                tab.set_needs_animation_frame()

    for child in node.children:
        style(child, rules, tab)

def cascade_priority(rule):
    if len(rule) == 3:
        _, selector, _ = rule
    else:
        selector, _ = rule
    return selector.priority

# --- 6. 布局模块 ---
WIDTH, HEIGHT = 800, 600
HSTEP, VSTEP = 13, 18
SCROLL_STEP = 100
INPUT_WIDTH_PX = 200

FONTS = {}

def get_font(size, weight, style):
    key = (weight, style)
    if key not in FONTS:
        if weight == "bold":
            skia_weight = skia.FontStyle.kBold_Weight
        else:
            skia_weight = skia.FontStyle.kNormal_Weight
        if style == "italic":
            skia_style = skia.FontStyle.kItalic_Slant
        else:
            skia_style = skia.FontStyle.kUpright_Slant
        skia_width = skia.FontStyle.kNormal_Width
        style_info = skia.FontStyle(skia_weight, skia_width, skia_style)
        FONTS[key] = skia.Typeface("Arial", style_info)
    return skia.Font(FONTS[key], size)

def linespace(font):
    metrics = font.getMetrics()
    return metrics.fDescent - metrics.fAscent

class Rect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def contains_point(self, x, y):
        return x >= self.left and x < self.right and y >= self.top and y < self.bottom

    def to_skia(self):
        return skia.Rect.MakeLTRB(self.left, self.top, self.right, self.bottom)

BLOCK_ELEMENTS = [
    "html", "body", "article", "section", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
    "footer", "address", "p", "hr", "pre", "blockquote",
    "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
    "figcaption", "main", "div", "table", "form", "fieldset",
    "legend", "details", "summary"
]

HIDDEN_ELEMENTS = ["head", "title", "script", "style"]

class DocumentLayout:
    def __init__(self, node):
        self.node = node
        self.parent = None
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = 0

    def layout(self, zoom=1.0):
        self.zoom = zoom
        self.width = WIDTH - 2 * HSTEP * zoom
        self.x = HSTEP * zoom
        self.y = VSTEP * zoom
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout(depth=0)
        self.height = child.height if child.height is not None else 0

    def paint(self):
        return []

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        return cmds

class BlockLayout:
    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = 0
        self.cursor_x = 0
        node.layout_objects.append(self)

    def layout_mode(self):
        if isinstance(self.node, Text):
            return "inline"
        elif any([
            isinstance(child, Element) and child.tag in BLOCK_ELEMENTS
            for child in self.node.children
        ]):
            return "block"
        elif self.node.children:
            return "inline"
        else:
            return "block"

    def layout(self, depth=0):
        if depth > 1000:
            print("Warning: Max layout depth exceeded for node", self.node)
            self.height = 0
            return

        self.zoom = getattr(self.parent, "zoom", 1.0)
        self.x = self.parent.x
        self.width = self.parent.width
        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        mode = self.layout_mode()
        if mode == "block":
            previous = None
            for child in self.node.children:
                if isinstance(child, Element) and child.tag in HIDDEN_ELEMENTS:
                    continue
                next = BlockLayout(child, self, previous)
                self.children.append(next)
                previous = next
            for child in self.children:
                child.layout(depth + 1)
            heights = [child.height for child in self.children if child.height is not None]
            self.height = sum(heights) if heights else 0
        else:
            self.new_line()
            self.recurse(self.node)
            for child in self.children:
                child.layout()
            heights = [child.height for child in self.children if child.height is not None]
            self.height = sum(heights) if heights else 0

    def recurse(self, node):
        if isinstance(node, Text):
            for word in node.text.split():
                self.word(node, word)
        else:
            if node.tag in HIDDEN_ELEMENTS:
                return
            if node.tag in ["input", "button"]:
                self.input(node)
                return
            if node.tag == "br":
                self.new_line()
            for child in node.children:
                self.recurse(child)

    def new_line(self):
        self.cursor_x = 0
        last_line = self.children[-1] if self.children else None
        new_line = LineLayout(self.node, self, last_line)
        self.children.append(new_line)

    def word(self, node, word):
        weight = node.style["font-weight"]
        style = "roman" if node.style["font-style"] == "normal" else "italic"
        size = int(float(node.style["font-size"][:-2]) * .75 * self.zoom)
        color = node.style["color"]
        font = get_font(size, weight, style)
        w = font.measureText(word)
        if self.cursor_x + w > self.width:
            self.new_line()
        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        text = TextLayout(node, word, line, previous_word)
        line.children.append(text)
        self.cursor_x += w + font.measureText(" ")

    def input(self, node):
        weight = node.style["font-weight"]
        style = "roman" if node.style["font-style"] == "normal" else "italic"
        size = int(float(node.style["font-size"][:-2]) * .75 * self.zoom)
        font = get_font(size, weight, style)
        w = INPUT_WIDTH_PX * self.zoom
        if node.tag == "button":
            text = input_text(node)
            w = font.measureText(text) + 2 * HSTEP * self.zoom
        if self.cursor_x + w > self.width:
            self.new_line()
        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        layout = InputLayout(node, line, previous_word)
        line.children.append(layout)
        self.cursor_x += w + font.measureText(" ")

    def self_rect(self):
        return Rect(self.x, self.y, self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color", "transparent")
        if bgcolor != "transparent":
            radius = parse_px(
                self.node.style.get("border-radius", "0px")
            ) * self.zoom
            cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))
        return cmds

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        cmds = paint_visual_effects(self.node, cmds, self.self_rect())
        paint_outline(self.node, cmds, self.self_rect(), self.zoom)
        return cmds

class LineLayout:
    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = 0

    def layout(self):
        self.zoom = getattr(self.parent, "zoom", 1.0)
        self.width = self.parent.width
        self.x = self.parent.x
        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        for word in self.children:
            word.layout()

        if not self.children:
            self.height = 0
            return

        max_ascent = max([-word.font.getMetrics().fAscent for word in self.children])
        baseline = self.y + 1.25 * max_ascent
        for word in self.children:
            word.y = baseline + word.font.getMetrics().fAscent
        max_descent = max([word.font.getMetrics().fDescent for word in self.children])
        self.height = 1.25 * (max_ascent + max_descent)

    def paint(self):
        return []

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        outline_rect = skia.Rect.MakeEmpty()
        outline_node = None
        for child in self.children:
            if (
                isinstance(child, TextLayout)
                and child.node.parent.is_focused
                and parse_outline(
                    child.node.parent.style.get("outline")
                )
            ):
                outline_rect.join(child.self_rect())
                outline_node = child.node.parent
        if outline_node:
            paint_outline(outline_node, cmds, outline_rect, self.zoom)
        return cmds

class TextLayout:
    def __init__(self, node, word, parent, previous):
        self.node = node
        self.word = word
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = 0
        self.font = None
        node.layout_objects.append(self)

    def layout(self):
        weight = self.node.style["font-weight"]
        style = "roman" if self.node.style["font-style"] == "normal" else "italic"
        self.zoom = getattr(self.parent, "zoom", 1.0)
        size = int(
            float(self.node.style["font-size"][:-2]) * .75 * self.zoom
        )
        self.font = get_font(size, weight, style)
        self.width = self.font.measureText(self.word)

        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def self_rect(self):
        return skia.Rect.MakeXYWH(
            self.x, self.y, self.width, self.height
        )

    def paint(self):
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        return cmds

def input_text(node):
    if node.tag == "input":
        return node.attributes.get("value", "")
    elif node.tag == "button":
        if len(node.children) == 1 and isinstance(node.children[0], Text):
            return node.children[0].text
        print("Ignoring HTML contents inside button")
        return ""
    return ""

class InputLayout:
    def __init__(self, node, parent, previous):
        self.node = node
        self.children = []
        self.parent = parent
        self.previous = previous
        self.x = None
        self.y = None
        self.width = None
        self.height = 0
        self.font = None
        node.layout_objects.append(self)

    def layout(self):
        weight = self.node.style["font-weight"]
        style = "roman" if self.node.style["font-style"] == "normal" else "italic"
        self.zoom = getattr(self.parent, "zoom", 1.0)
        size = int(
            float(self.node.style["font-size"][:-2]) * .75 * self.zoom
        )
        self.font = get_font(size, weight, style)
        if self.node.tag == "button":
            self.width = (
                self.font.measureText(input_text(self.node))
                + 2 * HSTEP * self.zoom
            )
        else:
            self.width = INPUT_WIDTH_PX * self.zoom

        if self.previous:
            space = self.previous.font.measureText(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = linespace(self.font)

    def self_rect(self):
        return Rect(self.x, self.y, self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color", "transparent")
        if bgcolor != "transparent":
            radius = parse_px(
                self.node.style.get("border-radius", "0px")
            ) * self.zoom
            cmds.append(DrawRRect(self.self_rect(), radius, bgcolor))
            cmds.append(DrawOutline(self.self_rect(), "black", 1))
        color = self.node.style["color"]
        text_x = self.x + HSTEP // 2
        text_y = self.y + max(0, (self.height - linespace(self.font)) // 2)
        cmds.append(DrawText(text_x, text_y, input_text(self.node), self.font, color))
        if self.node.is_focused and self.node.tag == "input":
            w = self.font.measureText(input_text(self.node))
            cmds.append(DrawLine(
                text_x + w, text_y,
                text_x + w, text_y + linespace(self.font),
                "red", 1
            ))
        return cmds

    def should_paint(self):
        return True

    def paint_effects(self, cmds):
        cmds = paint_visual_effects(self.node, cmds, self.self_rect())
        paint_outline(self.node, cmds, self.self_rect(), self.zoom)
        return cmds


def get_tabindex(node):
    try:
        tabindex = int(node.attributes.get("tabindex", "9999999"))
    except ValueError:
        tabindex = 9999999
    return 9999999 if tabindex == 0 else tabindex


def is_focusable(node):
    if not isinstance(node, Element):
        return False
    if get_tabindex(node) < 0:
        return False
    if "tabindex" in node.attributes:
        return True
    return node.tag in ["input", "button", "a"]


def parse_outline(outline):
    if not outline:
        return None
    values = outline.split()
    if len(values) != 3 or values[1].casefold() != "solid":
        return None
    try:
        thickness = parse_px(values[0])
    except (TypeError, ValueError):
        return None
    return thickness, values[2]


def paint_outline(node, cmds, rect, zoom=1.0):
    outline = parse_outline(node.style.get("outline"))
    if outline:
        thickness, color = outline
        cmds.append(DrawOutline(rect, color, thickness * zoom))

# --- 7. 绘制命令模块 ---
class DrawText:
    def __init__(self, x1, y1, text, font, color):
        self.rect = skia.Rect.MakeLTRB(
            x1, y1,
            x1 + font.measureText(text),
            y1 - font.getMetrics().fAscent + font.getMetrics().fDescent
        )
        self.text = text
        self.font = font
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
        )
        baseline = self.rect.top() - self.font.getMetrics().fAscent
        canvas.drawString(
            self.text, float(self.rect.left()),
            baseline, self.font, paint
        )

class DrawRect:
    def __init__(self, rect, color):
        self.rect = rect
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
        )
        canvas.drawRect(self.rect.to_skia(), paint)

class DrawRRect:
    def __init__(self, rect, radius, color):
        self.rect = rect
        self.rrect = skia.RRect.MakeRectXY(
            rect.to_skia(), radius, radius)
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
        )
        canvas.drawRRect(self.rrect, paint)

class DrawLine:
    def __init__(self, x1, y1, x2, y2, color, thickness):
        self.rect = skia.Rect.MakeLTRB(x1, y1, x2, y2)
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawLine(
            self.rect.left(), self.rect.top(),
            self.rect.right(), self.rect.bottom(),
            paint
        )

class DrawOutline:
    def __init__(self, rect, color, thickness):
        self.rect = rect
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        paint = skia.Paint(
            Color=parse_color(self.color),
            StrokeWidth=self.thickness,
            Style=skia.Paint.kStroke_Style,
        )
        rect = (
            self.rect
            if isinstance(self.rect, skia.Rect)
            else self.rect.to_skia()
        )
        canvas.drawRect(rect, paint)

class Blend:
    def __init__(self, opacity, blend_mode, children):
        self.opacity = opacity
        self.blend_mode = blend_mode
        self.should_save = self.blend_mode or self.opacity < 1
        self.children = children
        self.rect = skia.Rect.MakeEmpty()
        for cmd in self.children:
            if hasattr(cmd, "rect"):
                self.rect.join(cmd.rect if isinstance(cmd.rect, skia.Rect) else cmd.rect.to_skia())

    def execute(self, canvas):
        paint = skia.Paint(
            Alphaf=self.opacity,
            BlendMode=parse_blend_mode(self.blend_mode),
        )
        if self.should_save:
            canvas.saveLayer(None, paint)
        for cmd in self.children:
            cmd.execute(canvas)
        if self.should_save:
            canvas.restore()


class Transform:
    def __init__(self, translation, rect, children):
        self.translation = translation
        self.children = children
        self.rect = rect.to_skia() if isinstance(rect, Rect) else rect
        for child in self.children:
            if hasattr(child, "rect"):
                child_rect = (
                    child.rect
                    if isinstance(child.rect, skia.Rect)
                    else child.rect.to_skia()
                )
                self.rect.join(child_rect)

    def execute(self, canvas):
        if self.translation:
            x, y = self.translation
            canvas.save()
            canvas.translate(x, y)
        for cmd in self.children:
            cmd.execute(canvas)
        if self.translation:
            canvas.restore()

    def map(self, rect):
        return map_translation(rect, self.translation)

    def unmap(self, rect):
        return map_translation(rect, self.translation, reversed=True)


def paint_visual_effects(node, cmds, rect):
    opacity = float(node.style.get("opacity", "1.0"))
    blend_mode = node.style.get("mix-blend-mode")

    if node.style.get("overflow", "visible") == "clip":
        border_radius = parse_px(node.style.get("border-radius", "0px"))
        if not blend_mode:
            blend_mode = "source-over"
        cmds.append(Blend(1.0, "destination-in", [
            DrawRRect(rect, border_radius, "white")
        ]))

    blended = Blend(opacity, blend_mode, cmds)
    return [Transform(parse_transform(node.style.get("transform", "")),
                      rect, [blended])]

def paint_tree(layout_object, display_list):
    cmds = []
    if layout_object.should_paint():
        cmds = layout_object.paint()
    for child in layout_object.children:
        paint_tree(child, cmds)
    if layout_object.should_paint():
        cmds = layout_object.paint_effects(cmds)
    display_list.extend(cmds)


class AccessibilityNode:
    def __init__(self, node):
        self.node = node
        self.children = []
        self.text = ""
        self.bounds = self.compute_bounds()
        self.ignored = (
            isinstance(node, Element)
            and node.attributes.get("aria-hidden", "").casefold() == "true"
        )

        if isinstance(node, Text):
            if is_focusable(node.parent):
                self.role = "focusable text"
            else:
                self.role = "StaticText"
        elif self.ignored:
            self.role = "none"
        elif "role" in node.attributes:
            self.role = node.attributes["role"].casefold()
        elif node.tag == "a":
            self.role = "link"
        elif node.tag == "input":
            self.role = "textbox"
        elif node.tag == "button":
            self.role = "button"
        elif node.tag == "html":
            self.role = "document"
        elif node.tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            self.role = "heading"
        else:
            self.role = "focusable" if is_focusable(node) else "none"

    def compute_bounds(self):
        bounds = []
        for layout_object in getattr(self.node, "layout_objects", []):
            rect = absolute_bounds_for_obj(layout_object)
            if not rect.isEmpty():
                bounds.append(rect)

        if bounds:
            return bounds

        # Inline elements do not have their own layout object. Their bounds
        # come from all descendant text and inline controls, including nested
        # inline elements such as <a><b>link</b></a>.
        for descendant in tree_to_list(self.node, []):
            if descendant is self.node:
                continue
            for layout_object in getattr(descendant, "layout_objects", []):
                rect = absolute_bounds_for_obj(layout_object)
                if not rect.isEmpty():
                    bounds.append(rect)
        return bounds

    def build(self):
        for child_node in self.node.children:
            self.build_internal(child_node)

        aria_label = (
            self.node.attributes.get("aria-label")
            if isinstance(self.node, Element)
            else None
        )
        if aria_label:
            self.text = aria_label
        elif self.role == "StaticText":
            self.text = self.node.text.strip()
        elif self.role == "focusable text":
            self.text = "Focusable text: " + self.node.text
        elif self.role == "focusable":
            self.text = "Focusable element"
        elif self.role == "textbox":
            self.text = "Input box: " + self.node.attributes.get(
                "value", ""
            )
        elif self.role == "button":
            self.text = "Button"
        elif self.role == "link":
            self.text = "Link"
        elif self.role == "heading":
            self.text = "Heading"
        elif self.role == "alert":
            self.text = "Alert"
        elif self.role == "document":
            self.text = "Document"

        if getattr(self.node, "is_focused", False):
            self.text += " is focused"

    def build_internal(self, child_node):
        child = AccessibilityNode(child_node)
        if child.ignored:
            return
        if child.role != "none":
            self.children.append(child)
            child.build()
        else:
            for grandchild_node in child_node.children:
                self.build_internal(grandchild_node)

    def contains_point(self, x, y):
        return any(bound.contains(x, y) for bound in self.bounds)

    def hit_test(self, x, y):
        result = self if self.contains_point(x, y) else None
        for child in self.children:
            candidate = child.hit_test(x, y)
            if candidate:
                result = candidate
        return result

    def __repr__(self):
        return (
            "AccessibilityNode(role={}, text={!r}, bounds={})"
        ).format(self.role, self.text, self.bounds)


def speak_text(text):
    print("SPEAK:", text)


RUNTIME_JS = """
var NODES = {};
function Node(handle) { this.handle = handle; }
function get_node(handle) {
    if (!NODES[handle]) NODES[handle] = new Node(handle);
    return NODES[handle];
}
Node.prototype.getAttribute = function(attr) {
    return call_python("getAttribute", this.handle, attr);
}
Node.prototype.setAttribute = function(attr, value) {
    call_python("setAttribute", this.handle, attr, value.toString());
}
Object.defineProperty(Node.prototype, "style", {
    set: function(s) {
        call_python("style_set", this.handle, s.toString());
    }
});
Object.defineProperty(Node.prototype, "innerHTML", {
    set: function(s) {
        call_python("innerHTML_set", this.handle, s.toString());
    }
});

var LISTENERS = {};
Node.prototype.addEventListener = function(type, listener) {
    if (!LISTENERS[this.handle]) LISTENERS[this.handle] = {};
    var dict = LISTENERS[this.handle];
    if (!dict[type]) dict[type] = [];
    dict[type].push(listener);
}

var document = {
    querySelectorAll: function(selector) {
        var handles = call_python("querySelectorAll", selector);
        return handles.map(function(handle) { return get_node(handle); });
    }
};

var console = {
    log: function(x) { call_python("log", x); }
};

var SET_TIMEOUT_REQUESTS = {};
var RAF_LISTENERS = [];
var XHR_REQUESTS = {};

function setTimeout(callback, time_delta) {
    var handle = Object.keys(SET_TIMEOUT_REQUESTS).length;
    SET_TIMEOUT_REQUESTS[handle] = callback;
    call_python("setTimeout", handle, time_delta);
}

function __runSetTimeout(handle) {
    var callback = SET_TIMEOUT_REQUESTS[handle];
    callback();
}

function requestAnimationFrame(fn) {
    RAF_LISTENERS.push(fn);
    call_python("requestAnimationFrame");
}

function __runRAFHandlers() {
    var handlers_copy = RAF_LISTENERS;
    RAF_LISTENERS = [];
    for (var i = 0; i < handlers_copy.length; i++) {
        handlers_copy[i]();
    }
}

function Event(type) {
    this.type = type;
    this.do_default = true;
    this.defaultPrevented = false;
}
Event.prototype.preventDefault = function() {
    this.do_default = false;
    this.defaultPrevented = true;
}

Node.prototype.dispatchEvent = function(evt) {
    if (typeof evt == "string") evt = new Event(evt);
    var type = evt.type;
    var handle = this.handle;
    var list = (LISTENERS[handle] && LISTENERS[handle][type]) || [];
    evt.target = this;
    evt.currentTarget = this;
    for (var i = 0; i < list.length; i++) {
        list[i].call(this, evt);
    }
    return evt.do_default;
}

function dispatch_event(type, handle) {
    return get_node(handle).dispatchEvent(new Event(type));
}

function XMLHttpRequest() {
    this.handle = Object.keys(XHR_REQUESTS).length;
    XHR_REQUESTS[this.handle] = this;
}

XMLHttpRequest.prototype.open = function(method, url, is_async) {
    this.is_async = is_async;
    this.method = method;
    this.url = url;
}

XMLHttpRequest.prototype.send = function(body) {
    this.responseText = call_python(
        "XMLHttpRequest_send",
        this.method, this.url, body || "",
        this.is_async, this.handle);
}

function __runXHROnload(body, handle) {
    var obj = XHR_REQUESTS[handle];
    obj.responseText = body;
    if (obj.onload) {
        obj.onload(new Event("load"));
    }
}
0;
"""

class JSContext:
    def __init__(self, tab):
        self.tab = tab
        self.discarded = False
        self.interp = dukpy.JSInterpreter()
        self.node_to_handle = {}
        self.handle_to_node = {}
        self.interp.export_function("log", print)
        self.interp.export_function("querySelectorAll", self.querySelectorAll)
        self.interp.export_function("getAttribute", self.getAttribute)
        self.interp.export_function("setAttribute", self.setAttribute)
        self.interp.export_function("style_set", self.style_set)
        self.interp.export_function("innerHTML_set", self.innerHTML_set)
        self.interp.export_function("setTimeout", self.setTimeout)
        self.interp.export_function("requestAnimationFrame", self.requestAnimationFrame)
        self.interp.export_function("XMLHttpRequest_send", self.XMLHttpRequest_send)
        self.interp.evaljs(RUNTIME_JS)

    def run(self, script, code):
        try:
            return self.interp.evaljs(code)
        except dukpy.JSRuntimeError as e:
            print(f"Script {script} crashed", e)

    def dispatch_event(self, type, elt):
        handle = self.get_handle(elt)
        try:
            do_default = self.interp.evaljs(f"dispatch_event({type!r}, {handle})")
            return not do_default
        except dukpy.JSRuntimeError as e:
            print("Script event crashed", e)
            return False

    def get_handle(self, elt):
        if elt not in self.node_to_handle:
            handle = len(self.node_to_handle)
            self.node_to_handle[elt] = handle
            self.handle_to_node[handle] = elt
        return self.node_to_handle[elt]

    def querySelectorAll(self, selector_text):
        selector = CSSParser(selector_text).selector()
        return [
            self.get_handle(node)
            for node in tree_to_list(self.tab.nodes, [])
            if selector.matches(node)
        ]

    def getAttribute(self, handle, attr):
        elt = self.handle_to_node[handle]
        return elt.attributes.get(attr, "")

    def setAttribute(self, handle, attr, value):
        elt = self.handle_to_node[handle]
        elt.attributes[attr] = value
        if hasattr(self.tab, "set_needs_render"):
            self.tab.set_needs_render()

    def style_set(self, handle, value):
        elt = self.handle_to_node[handle]
        elt.attributes["style"] = value
        if hasattr(self.tab, "set_needs_render"):
            self.tab.set_needs_render()
        else:
            self.tab.render()

    def innerHTML_set(self, handle, s):
        elt = self.handle_to_node[handle]
        doc = HTMLParser("<html><body>" + s + "</body></html>").parse()
        body = next(
            node for node in tree_to_list(doc, [])
            if isinstance(node, Element) and node.tag == "body"
        )
        elt.children = body.children
        self.fix_parent_pointers(elt)
        if hasattr(self.tab, "set_needs_render"):
            self.tab.set_needs_render()
        else:
            self.tab.render()

    def dispatch_settimeout(self, handle):
        if self.discarded:
            return
        try:
            self.interp.evaljs("__runSetTimeout(dukpy.handle)", handle=handle)
        except dukpy.JSRuntimeError as e:
            print("setTimeout callback crashed", e)

    def setTimeout(self, handle, time_delta):
        def run_callback():
            task = Task(self.dispatch_settimeout, handle)
            self.tab.task_runner.schedule_task(task)

        timer = threading.Timer(float(time_delta) / 1000.0, run_callback)
        timer.daemon = True
        timer.start()

    def dispatch_xhr_onload(self, out, handle):
        if self.discarded:
            return
        try:
            self.interp.evaljs(
                "__runXHROnload(dukpy.out, dukpy.handle)",
                out=out,
                handle=handle,
            )
        except dukpy.JSRuntimeError as e:
            print("XHR onload crashed", e)

    def requestAnimationFrame(self):
        browser = getattr(self.tab, "browser", None)
        if browser:
            browser.request_animation_frame(self.tab)

    def XMLHttpRequest_send(self, method, url, body, is_async, handle):
        full_url = self.tab.url.resolve(url)
        if not self.tab.allowed_request(full_url):
            raise Exception("Cross-origin XHR blocked by CSP")
        if full_url.origin() != self.tab.url.origin():
            raise Exception("Cross-origin XHR request not allowed")

        def run_load():
            payload = body if method.casefold() == "post" else None
            _, out = full_url.request(self.tab.url, payload)
            if is_async:
                task = Task(self.dispatch_xhr_onload, out, handle)
                self.tab.task_runner.schedule_task(task)
            if not is_async:
                return out

        if not is_async:
            return run_load()

        thread = threading.Thread(target=run_load, daemon=True)
        thread.start()

    def fix_parent_pointers(self, node):
        for child in node.children:
            child.parent = node
            self.fix_parent_pointers(child)

# --- 8. 浏览器 Chrome ---
class Chrome:
    def __init__(self, browser):
        self.browser = browser
        self.font = get_font(20, "normal", "roman")
        self.font_height = linespace(self.font)
        self.padding = 5
        self.tabbar_top = 0
        self.tabbar_bottom = self.font_height + 2 * self.padding

        plus_width = self.font.measureText("+") + 2 * self.padding
        self.newtab_rect = Rect(
            self.padding, self.padding,
            self.padding + plus_width,
            self.padding + self.font_height
        )

        back_width = self.font.measureText("<") + 2 * self.padding
        self.urlbar_top = self.tabbar_bottom
        self.urlbar_bottom = self.urlbar_top + self.font_height + 2 * self.padding
        self.bottom = self.urlbar_bottom

        forward_width = self.font.measureText(">") + 2 * self.padding
        bookmark_width = self.font.measureText("*") + 2 * self.padding
        self.back_rect = Rect(
            self.padding,
            self.urlbar_top + self.padding,
            self.padding + back_width,
            self.urlbar_bottom - self.padding
        )
        self.forward_rect = Rect(
            self.back_rect.right + self.padding,
            self.urlbar_top + self.padding,
            self.back_rect.right + self.padding + forward_width,
            self.urlbar_bottom - self.padding
        )
        self.bookmark_rect = Rect(
            self.forward_rect.right + self.padding,
            self.urlbar_top + self.padding,
            self.forward_rect.right + self.padding + bookmark_width,
            self.urlbar_bottom - self.padding
        )
        self.address_rect = Rect(
            self.bookmark_rect.right + self.padding,
            self.urlbar_top + self.padding,
            WIDTH - self.padding,
            self.urlbar_bottom - self.padding
        )

        self.focus = None
        self.address_bar = ""

    def tab_rect(self, i):
        tabs_start = self.newtab_rect.right + self.padding
        tab_width = self.font.measureText("Tab X") + 2 * self.padding
        return Rect(
            tabs_start + tab_width * i, self.tabbar_top,
            tabs_start + tab_width * (i + 1), self.tabbar_bottom
        )

    def paint(self):
        cmds = []
        color = "white" if self.browser.dark_mode else "black"

        cmds.append(DrawRect(
            Rect(0, 0, WIDTH, self.bottom),
            "black" if self.browser.dark_mode else "white",
        ))
        cmds.append(DrawLine(0, self.bottom, WIDTH, self.bottom, color, 1))

        cmds.append(DrawOutline(self.newtab_rect, color, 1))
        cmds.append(DrawText(
            self.newtab_rect.left + self.padding,
            self.newtab_rect.top,
            "+", self.font, color
        ))

        for i, tab in enumerate(self.browser.tabs):
            bounds = self.tab_rect(i)
            cmds.append(DrawLine(bounds.left, 0, bounds.left, bounds.bottom, color, 1))
            cmds.append(DrawLine(bounds.right, 0, bounds.right, bounds.bottom, color, 1))
            cmds.append(DrawText(
                bounds.left + self.padding,
                bounds.top + self.padding,
                "Tab {}".format(i), self.font, color
            ))
            if tab == self.browser.active_tab:
                cmds.append(DrawLine(bounds.left, bounds.bottom, bounds.right, bounds.bottom, color, 1))
                cmds.append(DrawLine(bounds.right, bounds.bottom, WIDTH, bounds.bottom, color, 1))

        cmds.append(DrawOutline(self.back_rect, color, 1))
        cmds.append(DrawText(
            self.back_rect.left + self.padding,
            self.back_rect.top,
            "<", self.font, self.back_color()
        ))

        cmds.append(DrawOutline(self.forward_rect, self.forward_color(), 1))
        cmds.append(DrawText(
            self.forward_rect.left + self.padding,
            self.forward_rect.top,
            ">", self.font, self.forward_color()
        ))

        cmds.append(DrawOutline(self.bookmark_rect, color, 1))
        cmds.append(DrawText(
            self.bookmark_rect.left + self.padding,
            self.bookmark_rect.top,
            "*", self.font, self.bookmark_color()
        ))

        cmds.append(DrawOutline(self.address_rect, color, 1))
        if self.focus == "address bar":
            cmds.append(DrawText(
                self.address_rect.left + self.padding,
                self.address_rect.top,
                self.address_bar, self.font, color
            ))
            w = self.font.measureText(self.address_bar)
            cmds.append(DrawLine(
                self.address_rect.left + self.padding + w,
                self.address_rect.top,
                self.address_rect.left + self.padding + w,
                self.address_rect.bottom,
                "red", 1
            ))
        else:
            url = str(self.browser.active_tab.url)
            cmds.append(DrawText(
                self.address_rect.left + self.padding,
                self.address_rect.top,
                url, self.font, color
            ))

        return cmds

    def back_color(self):
        if self.browser.active_tab and self.browser.active_tab.can_go_back():
            return "white" if self.browser.dark_mode else "black"
        return "#777777"

    def forward_color(self):
        if self.browser.active_tab and self.browser.active_tab.can_go_forward():
            return "white" if self.browser.dark_mode else "black"
        return "#777777"

    def bookmark_color(self):
        if self.browser.is_current_page_bookmarked():
            return "gold"
        return "white" if self.browser.dark_mode else "black"

    def click(self, x, y):
        self.focus = None
        if self.newtab_rect.contains_point(x, y):
            self.browser.new_tab(URL("https://browser.engineering/"))
        elif self.back_rect.contains_point(x, y) and self.browser.active_tab:
            self.browser.active_tab.go_back()
            self.browser.set_title()
        elif self.forward_rect.contains_point(x, y) and self.browser.active_tab:
            self.browser.active_tab.go_forward()
            self.browser.set_title()
        elif self.bookmark_rect.contains_point(x, y):
            self.browser.toggle_bookmark()
        elif self.address_rect.contains_point(x, y):
            if self.browser.active_tab:
                self.browser.active_tab.blur()
            self.focus = "address bar"
            self.address_bar = ""
        else:
            for i, tab in enumerate(self.browser.tabs):
                if self.tab_rect(i).contains_point(x, y):
                    self.browser.set_active_tab(tab)
                    break

    def focus_addressbar(self):
        self.focus = "address bar"
        self.address_bar = ""

    def blur(self):
        self.focus = None

    def keypress(self, char):
        if self.focus == "address bar":
            self.address_bar += char

    def backspace(self):
        if self.focus == "address bar" and len(self.address_bar) > 0:
            self.address_bar = self.address_bar[:-1]

    def enter(self):
        if self.focus == "address bar" and len(self.address_bar) > 0:
            try:
                url = URL(self.address_bar)
                self.browser.schedule_load(url)
                self.focus = None
                self.browser.set_needs_raster_and_draw()
            except Exception as e:
                print(f"导航失败: {e}")

# --- 9. Tab 和 Browser ---
DEFAULT_STYLE_SHEET = CSSParser("""
    pre { background-color: gray; }
    a { color: blue; }
    i { font-style: italic; }
    b { font-weight: bold; }
    small { font-size: 90%; }
    big { font-size: 110%; }
    input { font-size: 16px; font-weight: normal; font-style: normal; background-color: lightblue; }
    button { font-size: 16px; font-weight: normal; font-style: normal; background-color: orange; }
    input:focus { outline: 2px solid black; }
    button:focus { outline: 2px solid black; }
    a:focus { outline: 2px solid black; }
    div:focus { outline: 2px solid black; }
    @media (prefers-color-scheme: dark) {
        a { color: lightblue; }
        input { background-color: #2222ff; color: white; }
        button { background-color: #992500; color: white; }
        input:focus { outline: 2px solid white; }
        button:focus { outline: 2px solid white; }
        a:focus { outline: 2px solid white; }
        div:focus { outline: 2px solid white; }
    }
""").parse()

class Tab:
    def __init__(self, tab_height):
        self.tab_height = tab_height
        self.browser = None
        self.url = None
        self.scroll = 0
        self.scroll_changed_in_tab = False
        self.history = []
        self.forward_history = []
        self.display_list = []
        self.document = None
        self.bookmarks = set()
        self.focus = None
        self.needs_focus_scroll = False
        self.rules = DEFAULT_STYLE_SHEET.copy()
        self.js = None
        self.allowed_origins = None
        self.zoom = 1.0
        self.dark_mode = False
        self.needs_accessibility = True
        self.accessibility_tree = None
        self.needs_render = True
        self.needs_style = True
        self.needs_layout = True
        self.needs_paint = True
        self.needs_animation_frame = False
        self.animation_frame_scheduled = False
        self.next_animation_frame = 0.0
        self.task_runner = TaskRunner(self)
        self.loaded = False

    def allowed_request(self, url):
        return self.allowed_origins is None or url.origin() in self.allowed_origins

    def load(self, url, add_history=True, bookmarks=None, payload=None):
        self.loaded = False
        self.focus_element(None)
        self.zoom = 1.0
        self.task_runner.clear_pending_tasks()
        if self.js:
            self.js.discarded = True
        referrer = self.url
        if bookmarks is not None:
            self.bookmarks = bookmarks
        try:
            if url.scheme == "about":
                headers, body = {}, self.about_page(url)
            else:
                headers, body = url.request(referrer, payload)
            if len(body) == 0:
                return
        except Exception as e:
            print(f"请求失败: {e}")
            return

        self.url = url
        if add_history:
            self.history.append(url)
            self.forward_history = []
        self.allowed_origins = None
        if "content-security-policy" in headers:
            csp = headers["content-security-policy"].split()
            if len(csp) > 0 and csp[0] == "default-src":
                self.allowed_origins = []
                for origin in csp[1:]:
                    self.allowed_origins.append(URL(origin).origin())

        self.nodes = HTMLParser(body).parse()
        self.accessibility_tree = None
        self.js = JSContext(self)

        scripts = [
            node.attributes["src"]
            for node in tree_to_list(self.nodes, [])
            if isinstance(node, Element)
            and node.tag == "script"
            and "src" in node.attributes
        ]
        for script in scripts:
            script_url = url.resolve(script)
            if not self.allowed_request(script_url):
                print("Blocked script", script, "due to CSP")
                continue
            try:
                _, body = script_url.request(url)
            except:
                continue
            self.task_runner.schedule_task(Task(self.js.run, script, body))

        rules = DEFAULT_STYLE_SHEET.copy()
        links = [
            node.attributes["href"]
            for node in tree_to_list(self.nodes, [])
            if isinstance(node, Element)
            and node.tag == "link"
            and node.attributes.get("rel") == "stylesheet"
            and "href" in node.attributes
        ]
        for link in links:
            try:
                style_url = url.resolve(link)
                if not self.allowed_request(style_url):
                    print("Blocked style", link, "due to CSP")
                    continue
                _, body = style_url.request(url)
                rules.extend(CSSParser(body).parse())
            except:
                continue

        self.rules = rules
        self.scroll = 0
        self.scroll_changed_in_tab = True
        self.needs_render = True
        self.needs_style = True
        self.needs_layout = True
        self.needs_paint = True
        self.needs_accessibility = True
        self.render()
        self.loaded = True
        if self.browser:
            self.browser.set_needs_raster_and_draw()

    def about_page(self, url):
        if url.path != "bookmarks":
            return "<html><body><h1>Not Found</h1></body></html>"
        items = []
        for bookmark in sorted(self.bookmarks):
            items.append(f'<li><a href="{bookmark}">{bookmark}</a></li>')
        if not items:
            items.append("<li>No bookmarks yet</li>")
        return (
            "<html><head><title>Bookmarks</title></head>"
            "<body><h1>Bookmarks</h1><ul>"
            + "".join(items)
            + "</ul></body></html>"
        )

    def raster(self, canvas):
        self.render()
        for cmd in self.display_list:
            cmd.execute(canvas)

    def set_needs_render(self):
        self.needs_render = True
        self.needs_style = True
        self.needs_layout = True
        self.needs_paint = True
        self.set_needs_animation_frame(immediate=True)

    def set_needs_animation_frame(self, immediate=False):
        self.needs_animation_frame = True
        if self.browser:
            self.browser.request_animation_frame(self, immediate=immediate)

    def run_animation_frame(self):
        if self.js and not self.js.discarded:
            try:
                self.js.interp.evaljs("__runRAFHandlers()")
            except dukpy.JSRuntimeError as e:
                print("requestAnimationFrame callback crashed", e)

        # A script callback can change style or DOM content. Resolve those
        # changes before advancing transitions so a transition starts from
        # the style visible at the beginning of this frame.
        self.render()
        if self.needs_focus_scroll and self.focus:
            self.scroll_to(self.focus)
        self.needs_focus_scroll = False

        active_animations = False
        for node in tree_to_list(self.nodes, []):
            for property_name, animation in list(node.animations.items()):
                node.style[property_name] = animation.animate()
                self.needs_paint = True
                if animation.done:
                    del node.animations[property_name]
                else:
                    active_animations = True

        if active_animations:
            self.set_needs_animation_frame()
        self.render()
        self.scroll_changed_in_tab = False
        if self.browser:
            self.browser.set_needs_raster_and_draw()

    def scrolldown(self):
        if not self.document:
            return
        new_scroll = self.clamp_scroll(self.scroll + SCROLL_STEP)
        if new_scroll != self.scroll:
            self.scroll = new_scroll
            self.scroll_changed_in_tab = True
            if self.browser:
                self.browser.set_needs_raster_and_draw()

    def clamp_scroll(self, scroll):
        if not self.document:
            return max(0, scroll)
        document_height = self.document.height + 2 * VSTEP * self.zoom
        max_scroll = max(0, document_height - self.tab_height)
        return max(0, min(scroll, max_scroll))

    def scroll_to(self, elt):
        if not self.document:
            return
        layout_objects = [
            obj for obj in tree_to_list(self.document, [])
            if getattr(obj, "node", None) is elt
        ]
        if not layout_objects:
            return

        bounds = [
            absolute_bounds_for_obj(obj)
            for obj in layout_objects
        ]
        bounds = [rect for rect in bounds if not rect.isEmpty()]
        if not bounds:
            return
        top = min(rect.top() for rect in bounds)
        bottom = max(rect.bottom() for rect in bounds)
        viewport_top = self.scroll
        viewport_bottom = self.scroll + self.tab_height
        if top >= viewport_top and bottom <= viewport_bottom:
            return

        self.scroll = self.clamp_scroll(top - SCROLL_STEP)
        self.scroll_changed_in_tab = True

    def click(self, x, y):
        self.render()
        elt = self.hit_test(x, y)
        if not elt:
            self.focus_element(None)
            return
        while elt:
            if isinstance(elt, Text):
                pass
            elif is_focusable(elt):
                if self.js and self.js.dispatch_event("click", elt):
                    return
                self.focus_element(elt)
                if elt.tag == "a" and "href" in elt.attributes:
                    url = self.url.resolve(elt.attributes["href"])
                    self.load(url, bookmarks=self.bookmarks)
                elif elt.tag == "button":
                    self.submit_form(elt)
                return
            elt = elt.parent

    def middle_click(self, x, y):
        return self.url_at(x, y)

    def url_at(self, x, y):
        elt = self.hit_test(x, y)
        while elt:
            if isinstance(elt, Element) and elt.tag == "a" and "href" in elt.attributes:
                return self.url.resolve(elt.attributes["href"])
            elt = elt.parent
        return None

    def hit_test(self, x, y):
        if not self.document:
            return None
        y += self.scroll
        objs = [
            obj for obj in tree_to_list(self.document, [])
            if hasattr(obj, 'x') and hasattr(obj, 'y')
            and hasattr(obj, 'width') and hasattr(obj, 'height')
            and obj.x is not None and obj.y is not None
            and absolute_bounds_for_obj(obj).left() <= x
            and x < absolute_bounds_for_obj(obj).right()
            and absolute_bounds_for_obj(obj).top() <= y
            and y < absolute_bounds_for_obj(obj).bottom()
        ]
        if not objs:
            return None
        return objs[-1].node

    def submit_form(self, elt):
        while elt:
            if isinstance(elt, Element) and elt.tag == "form":
                break
            elt = elt.parent
        if not elt:
            return
        if self.js and self.js.dispatch_event("submit", elt):
            return
        inputs = [
            node for node in tree_to_list(elt, [])
            if isinstance(node, Element)
            and node.tag == "input"
            and "name" in node.attributes
        ]
        body = ""
        for input in inputs:
            name = input.attributes["name"]
            value = input.attributes.get("value", "")
            body += "&" + urllib.parse.quote_plus(name)
            body += "=" + urllib.parse.quote_plus(value)
        body = body[1:]
        action = elt.attributes.get("action", self.url.path)
        url = self.url.resolve(action)
        method = elt.attributes.get("method", "get").casefold()
        if method == "post":
            self.load(url, bookmarks=self.bookmarks, payload=body)
        else:
            separator = "&" if "?" in url.path else "?"
            self.load(URL(str(url) + (separator + body if body else "")), bookmarks=self.bookmarks)

    def keypress(self, char):
        if self.focus and self.focus.tag == "input":
            if self.js and self.js.dispatch_event("keydown", self.focus):
                return
            self.focus.attributes["value"] = self.focus.attributes.get("value", "") + char
            self.set_needs_render()

    def backspace(self):
        if self.focus and self.focus.tag == "input":
            self.focus.attributes["value"] = self.focus.attributes.get("value", "")[:-1]
            self.set_needs_render()

    def enter(self):
        if not self.focus:
            return
        if self.js and self.js.dispatch_event("click", self.focus):
            return
        if self.focus.tag == "input":
            self.focus.attributes.setdefault("value", "")
            self.set_needs_render()
        elif self.focus.tag == "a" and "href" in self.focus.attributes:
            self.load(
                self.url.resolve(self.focus.attributes["href"]),
                bookmarks=self.bookmarks,
            )
        elif self.focus.tag == "button":
            self.submit_form(self.focus)

    def blur(self):
        self.focus_element(None)

    def focus_element(self, node):
        if node and node != self.focus:
            self.needs_focus_scroll = True
        if self.focus:
            self.focus.is_focused = False
        self.focus = node
        if node:
            node.is_focused = True
        self.set_needs_render()

    def advance_tab(self):
        focusable_nodes = [
            node for node in tree_to_list(self.nodes, [])
            if isinstance(node, Element) and is_focusable(node)
        ]
        focusable_nodes.sort(key=get_tabindex)
        if self.focus in focusable_nodes:
            index = focusable_nodes.index(self.focus) + 1
        else:
            index = 0
        if index < len(focusable_nodes):
            self.focus_element(focusable_nodes[index])
            if self.browser:
                self.browser.focus_content()
        else:
            self.focus_element(None)
            if self.browser:
                self.browser.focus_addressbar()

    def zoom_by(self, increment):
        if increment:
            self.zoom *= 1.1
            self.scroll *= 1.1
        else:
            self.zoom /= 1.1
            self.scroll /= 1.1
        self.scroll_changed_in_tab = True
        self.set_needs_render()

    def reset_zoom(self):
        self.scroll /= self.zoom
        self.zoom = 1.0
        self.scroll_changed_in_tab = True
        self.set_needs_render()

    def set_dark_mode(self, value):
        self.dark_mode = value
        self.set_needs_render()

    def render(self):
        if not (
            self.needs_render
            or self.needs_style
            or self.needs_layout
            or self.needs_paint
        ) and self.document is not None:
            return
        if not getattr(self, "nodes", None):
            return
        if self.needs_style:
            style(self.nodes, sorted(self.rules, key=cascade_priority), self)
            self.needs_style = False
            self.needs_layout = True
        if self.needs_layout:
            self.document = DocumentLayout(self.nodes)
            self.document.layout(self.zoom)
            self.needs_layout = False
            self.needs_paint = True
            self.needs_accessibility = True
        if self.needs_paint:
            self.display_list = []
            paint_tree(self.document, self.display_list)
            self.needs_paint = False
        if self.needs_accessibility:
            self.accessibility_tree = AccessibilityNode(self.nodes)
            self.accessibility_tree.build()
            self.needs_accessibility = False
        self.needs_render = (
            self.needs_style or self.needs_layout or self.needs_paint
        )
        clamped_scroll = self.clamp_scroll(self.scroll)
        if clamped_scroll != self.scroll:
            self.scroll = clamped_scroll
            self.scroll_changed_in_tab = True
        if self.browser:
            self.browser.set_needs_raster_and_draw()

    def can_go_back(self):
        return len(self.history) > 1

    def can_go_forward(self):
        return len(self.forward_history) > 0

    def go_back(self):
        if not self.can_go_back():
            return
        current = self.history.pop()
        self.forward_history.append(current)
        back = self.history[-1]
        self.load(back, add_history=False)

    def go_forward(self):
        if not self.can_go_forward():
            return
        forward = self.forward_history.pop()
        self.history.append(forward)
        self.load(forward, add_history=False)

class Browser:
    def __init__(self):
        self.chrome = Chrome(self)
        self.sdl_window = sdl2.SDL_CreateWindow(
            b"Browser",
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            WIDTH,
            HEIGHT,
            sdl2.SDL_WINDOW_SHOWN,
        )
        self.root_surface = skia.Surface.MakeRaster(
            skia.ImageInfo.Make(
                WIDTH,
                HEIGHT,
                ct=skia.kRGBA_8888_ColorType,
                at=skia.kUnpremul_AlphaType,
            )
        )
        self.chrome_surface = skia.Surface(WIDTH, math.ceil(self.chrome.bottom))
        self.tab_surface = None
        self.tabs = []
        self.active_tab = None
        self.bookmarks = set()
        self.dark_mode = False
        self.accessibility_is_on = False
        self.accessibility_tree = None
        self.has_spoken_document = False
        self.last_spoken_focus = None
        self.spoken_alerts = {}
        self.muted = True
        self.needs_raster_and_draw = True

        if sdl2.SDL_BYTEORDER == sdl2.SDL_BIG_ENDIAN:
            self.RED_MASK = 0xff000000
            self.GREEN_MASK = 0x00ff0000
            self.BLUE_MASK = 0x0000ff00
            self.ALPHA_MASK = 0x000000ff
        else:
            self.RED_MASK = 0x000000ff
            self.GREEN_MASK = 0x0000ff00
            self.BLUE_MASK = 0x00ff0000
            self.ALPHA_MASK = 0xff000000

    def handle_click(self, e):
        if e.y < self.chrome.bottom:
            self.chrome.click(e.x, e.y)
            self.set_needs_raster_and_draw()
        else:
            if self.active_tab:
                tab = self.active_tab
                self.schedule_task(
                    tab,
                    self._handle_tab_click,
                    e.x,
                    e.y - self.chrome.bottom,
                )
        self.set_needs_raster_and_draw()

    def _handle_tab_click(self, x, y):
        if self.active_tab:
            self.active_tab.click(x, y)
            self.set_title()

    def handle_middle_click(self, e):
        if e.y >= self.chrome.bottom and self.active_tab:
            url = self.active_tab.middle_click(e.x, e.y - self.chrome.bottom)
            if url:
                self.new_tab(url)
        self.set_needs_raster_and_draw()

    def handle_key(self, char):
        if len(char) == 0:
            return
        if 0x20 <= ord(char) < 0x7f:
            if self.chrome.focus:
                self.chrome.keypress(char)
            elif self.active_tab:
                tab = self.active_tab
                self.schedule_task(tab, tab.keypress, char)
            self.set_needs_raster_and_draw()
            return
        if char in ["。", "．"]:
            if self.chrome.focus:
                self.chrome.keypress('.')
            elif self.active_tab:
                tab = self.active_tab
                self.schedule_task(tab, tab.keypress, ".")
            self.set_needs_raster_and_draw()

    def handle_backspace(self):
        if self.chrome.focus:
            self.chrome.backspace()
        elif self.active_tab:
            tab = self.active_tab
            self.schedule_task(tab, tab.backspace)
        self.set_needs_raster_and_draw()

    def handle_down(self):
        if self.active_tab:
            tab = self.active_tab
            self.schedule_task(tab, tab.scrolldown)
        self.set_needs_raster_and_draw()

    def handle_tab(self):
        if not self.active_tab:
            return
        self.schedule_task(self.active_tab, self.active_tab.advance_tab)
        self.set_needs_raster_and_draw()

    def handle_enter(self):
        if self.chrome.focus:
            self.chrome.enter()
        elif self.active_tab:
            self.schedule_task(self.active_tab, self.active_tab.enter)
        self.set_needs_raster_and_draw()

    def focus_content(self):
        self.chrome.blur()

    def focus_addressbar(self):
        if self.active_tab:
            self.active_tab.blur()
        self.chrome.focus_addressbar()
        if self.accessibility_is_on:
            self.speak_text("Address bar focused")
        self.set_needs_raster_and_draw()

    def increment_zoom(self, increment):
        if self.active_tab:
            self.schedule_task(self.active_tab, self.active_tab.zoom_by, increment)
            self.set_needs_raster_and_draw()

    def reset_zoom(self):
        if self.active_tab:
            self.schedule_task(self.active_tab, self.active_tab.reset_zoom)
            self.set_needs_raster_and_draw()

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        for tab in self.tabs:
            self.schedule_task(tab, tab.set_dark_mode, self.dark_mode)
        self.set_needs_raster_and_draw()

    def toggle_accessibility(self):
        self.accessibility_is_on = not self.accessibility_is_on
        self.has_spoken_document = False
        self.last_spoken_focus = None
        self.spoken_alerts = {}
        if self.accessibility_is_on:
            self.set_needs_raster_and_draw()

    def speak_text(self, text):
        if not text:
            return
        if self.muted:
            print(text)
        else:
            speak_text(text)

    def accessible_text(self, node):
        parts = []
        if node.text:
            parts.append(node.text)
        for child in node.children:
            if child.role == "StaticText" and child.text:
                parts.append(child.text)
        return " ".join(parts)

    def speak_node(self, node, prefix=""):
        if not node:
            return
        self.speak_text(prefix + self.accessible_text(node))

    def speak_document(self):
        if not self.accessibility_tree:
            return
        lines = ["Here are the document contents:"]
        for node in tree_to_list(self.accessibility_tree, []):
            if node.text:
                lines.append(node.text)
        self.speak_text("\n".join(lines))

    def update_accessibility(self):
        if not self.accessibility_is_on:
            return
        if not self.accessibility_tree and self.active_tab:
            self.active_tab.render()
            self.accessibility_tree = self.active_tab.accessibility_tree
        if not self.accessibility_tree:
            return

        if not self.has_spoken_document:
            self.speak_document()
            self.has_spoken_document = True

        active_alerts = [
            node for node in tree_to_list(self.accessibility_tree, [])
            if node.role == "alert"
        ]
        self.active_alerts = active_alerts
        current_alerts = {}
        for alert in active_alerts:
            alert_text = self.accessible_text(alert)
            current_alerts[alert.node] = alert_text
            previous_text = self.spoken_alerts.get(alert.node)
            if previous_text is None:
                self.speak_node(alert, "New alert: ")
            elif previous_text != alert_text:
                self.speak_node(alert, "Alert updated: ")
        self.spoken_alerts = current_alerts

        focused_node = self.active_tab.focus if self.active_tab else None
        if focused_node is None:
            self.last_spoken_focus = None
        elif focused_node is not self.last_spoken_focus:
            focused_a11y_node = next(
                (
                    node for node in tree_to_list(
                        self.accessibility_tree, []
                    )
                    if node.node is focused_node
                ),
                None,
            )
            if focused_a11y_node:
                self.speak_node(focused_a11y_node, "Element focused: ")
            self.last_spoken_focus = focused_node

    def toggle_mute(self):
        self.muted = not self.muted
        print("Screen reader muted:", self.muted)

    def go_back(self):
        if self.active_tab:
            self.schedule_task(self.active_tab, self.active_tab.go_back)
            self.set_needs_raster_and_draw()

    def cycle_tabs(self):
        if not self.tabs or not self.active_tab:
            return
        index = self.tabs.index(self.active_tab)
        self.set_active_tab(self.tabs[(index + 1) % len(self.tabs)])

    def schedule_task(self, tab, callback, *args):
        tab.task_runner.schedule_task(Task(callback, *args))

    def run_one_task(self):
        if not self.active_tab:
            return False
        ran = self.active_tab.task_runner.run_one()
        if ran:
            self.set_title()
            self.set_needs_raster_and_draw()
        return ran

    def request_animation_frame(self, tab, immediate=False):
        tab.needs_animation_frame = True
        if tab is not self.active_tab:
            return
        now = time.monotonic()
        if tab.animation_frame_scheduled:
            return
        if immediate:
            tab.next_animation_frame = now
        elif tab.next_animation_frame <= now:
            tab.next_animation_frame = now + REFRESH_RATE_SEC

    def _run_animation_frame_task(self, tab):
        try:
            if tab is self.active_tab:
                tab.run_animation_frame()
        finally:
            tab.animation_frame_scheduled = False
            if tab.needs_animation_frame:
                tab.next_animation_frame = max(
                    tab.next_animation_frame,
                    time.monotonic() + REFRESH_RATE_SEC,
                )

    def maybe_schedule_animation_frame(self):
        tab = self.active_tab
        if not tab or not tab.needs_animation_frame:
            return False
        if tab.animation_frame_scheduled:
            return False
        if time.monotonic() < tab.next_animation_frame:
            return False
        tab.needs_animation_frame = False
        tab.animation_frame_scheduled = True
        self.schedule_task(tab, self._run_animation_frame_task, tab)
        return True

    def schedule_load(self, url, payload=None):
        if not self.active_tab:
            return
        self.active_tab.task_runner.clear_pending_tasks()
        self.schedule_task(
            self.active_tab,
            self.active_tab.load,
            url,
            True,
            self.bookmarks,
            payload,
        )

    def set_active_tab(self, tab):
        self.active_tab = tab
        self.chrome.blur()
        tab.dark_mode = self.dark_mode
        tab.set_needs_render()
        if tab.needs_render or tab.needs_animation_frame:
            self.request_animation_frame(tab, immediate=True)
        self.set_title()
        self.set_needs_raster_and_draw()

    def new_tab(self, url):
        new_tab = Tab(HEIGHT - self.chrome.bottom)
        new_tab.browser = self
        new_tab.dark_mode = self.dark_mode
        self.tabs.append(new_tab)
        self.active_tab = new_tab
        new_tab.load(url, bookmarks=self.bookmarks)
        self.set_title()
        self.set_needs_raster_and_draw()

    def is_current_page_bookmarked(self):
        if not self.active_tab or not self.active_tab.url:
            return False
        return str(self.active_tab.url) in self.bookmarks

    def toggle_bookmark(self):
        if not self.active_tab or not self.active_tab.url:
            return
        url = str(self.active_tab.url)
        if url == "about:bookmarks":
            return
        if url in self.bookmarks:
            self.bookmarks.remove(url)
        else:
            self.bookmarks.add(url)

    def set_needs_raster_and_draw(self):
        self.needs_raster_and_draw = True

    def raster_and_draw(self):
        if not self.needs_raster_and_draw:
            return False
        self.raster_chrome()
        self.raster_tab()
        self.draw()
        self.update_accessibility()
        self.needs_raster_and_draw = False
        return True

    def set_title(self):
        if not self.active_tab or not self.active_tab.url:
            return
        title = str(self.active_tab.url)
        if getattr(self.active_tab, "nodes", None):
            for node in tree_to_list(self.active_tab.nodes, []):
                if isinstance(node, Element) and node.tag == "title":
                    texts = []
                    for child in node.children:
                        if isinstance(child, Text):
                            texts.append(child.text.strip())
                    if texts:
                        title = " ".join(texts)
                    break
        sdl2.SDL_SetWindowTitle(self.sdl_window, title.encode("utf-8"))

    def raster_tab(self):
        if not self.active_tab or not getattr(self.active_tab, "document", None):
            return
        tab_height = math.ceil(
            self.active_tab.document.height
            + 2 * VSTEP * self.active_tab.zoom
        )
        tab_height = max(1, tab_height)
        if not self.tab_surface or tab_height != self.tab_surface.height():
            self.tab_surface = skia.Surface(WIDTH, tab_height)
        canvas = self.tab_surface.getCanvas()
        canvas.clear(
            skia.ColorBLACK if self.dark_mode else skia.ColorWHITE
        )
        self.active_tab.raster(canvas)
        self.accessibility_tree = self.active_tab.accessibility_tree

    def raster_chrome(self):
        canvas = self.chrome_surface.getCanvas()
        canvas.clear(
            skia.ColorBLACK if self.dark_mode else skia.ColorWHITE
        )
        for cmd in self.chrome.paint():
            cmd.execute(canvas)

    def draw(self):
        canvas = self.root_surface.getCanvas()
        canvas.clear(
            skia.ColorBLACK if self.dark_mode else skia.ColorWHITE
        )

        if self.active_tab and self.tab_surface:
            tab_rect = skia.Rect.MakeLTRB(0, self.chrome.bottom, WIDTH, HEIGHT)
            tab_offset = self.chrome.bottom - self.active_tab.scroll
            canvas.save()
            canvas.clipRect(tab_rect)
            canvas.translate(0, tab_offset)
            self.tab_surface.draw(canvas, 0, 0)
            canvas.restore()

        chrome_rect = skia.Rect.MakeLTRB(0, 0, WIDTH, self.chrome.bottom)
        canvas.save()
        canvas.clipRect(chrome_rect)
        self.chrome_surface.draw(canvas, 0, 0)
        canvas.restore()

        skia_image = self.root_surface.makeImageSnapshot()
        skia_bytes = skia_image.tobytes()
        depth = 32
        pitch = 4 * WIDTH
        sdl_surface = sdl2.SDL_CreateRGBSurfaceFrom(
            skia_bytes, WIDTH, HEIGHT, depth, pitch,
            self.RED_MASK, self.GREEN_MASK, self.BLUE_MASK, self.ALPHA_MASK
        )
        rect = sdl2.SDL_Rect(0, 0, WIDTH, HEIGHT)
        window_surface = sdl2.SDL_GetWindowSurface(self.sdl_window)
        sdl2.SDL_BlitSurface(sdl_surface, rect, window_surface, rect)
        sdl2.SDL_UpdateWindowSurface(self.sdl_window)

    def handle_quit(self):
        for tab in self.tabs:
            tab.task_runner.set_needs_quit()
        sdl2.SDL_DestroyWindow(self.sdl_window)

def mainloop(browser):
    event = sdl2.SDL_Event()
    sdl2.SDL_StartTextInput()
    ctrl_down = False
    while True:
        got_event = sdl2.SDL_PollEvent(ctypes.byref(event)) != 0
        if got_event:
            if event.type == sdl2.SDL_QUIT:
                browser.handle_quit()
                sdl2.SDL_Quit()
                sys.exit()
            elif event.type == sdl2.SDL_MOUSEBUTTONUP:
                browser.handle_click(event.button)
            elif event.type == sdl2.SDL_KEYDOWN:
                key = event.key.keysym.sym
                if ctrl_down:
                    if key == sdl2.SDLK_EQUALS:
                        browser.increment_zoom(True)
                    elif key == sdl2.SDLK_MINUS:
                        browser.increment_zoom(False)
                    elif key == sdl2.SDLK_0:
                        browser.reset_zoom()
                    elif key == sdl2.SDLK_LEFT:
                        browser.go_back()
                    elif key == sdl2.SDLK_l:
                        browser.focus_addressbar()
                    elif key == sdl2.SDLK_t:
                        browser.new_tab(URL("https://browser.engineering/"))
                    elif key == sdl2.SDLK_TAB:
                        browser.cycle_tabs()
                    elif key == sdl2.SDLK_d:
                        browser.toggle_dark_mode()
                    elif key == sdl2.SDLK_a:
                        browser.toggle_accessibility()
                    elif key == sdl2.SDLK_m:
                        browser.toggle_mute()
                    elif key == sdl2.SDLK_q:
                        browser.handle_quit()
                        sdl2.SDL_Quit()
                        sys.exit()
                elif key == sdl2.SDLK_RETURN:
                    browser.handle_enter()
                elif key == sdl2.SDLK_BACKSPACE:
                    browser.handle_backspace()
                elif key == sdl2.SDLK_DOWN:
                    browser.handle_down()
                elif key == sdl2.SDLK_TAB:
                    browser.handle_tab()
                elif key in [sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL]:
                    ctrl_down = True
            elif event.type == sdl2.SDL_KEYUP:
                if event.key.keysym.sym in [
                    sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL
                ]:
                    ctrl_down = False
            elif event.type == sdl2.SDL_TEXTINPUT:
                if not ctrl_down:
                    browser.handle_key(event.text.text.decode("utf8"))

        ran_task = browser.run_one_task()
        browser.maybe_schedule_animation_frame()
        browser.raster_and_draw()

        if not got_event and not ran_task:
            delay_ms = 1
            tab = browser.active_tab
            if tab and tab.needs_animation_frame and not tab.animation_frame_scheduled:
                remaining = max(
                    0.0,
                    tab.next_animation_frame - time.monotonic(),
                )
                delay_ms = min(10, max(1, math.ceil(remaining * 1000)))
            sdl2.SDL_Delay(delay_ms)

if __name__ == "__main__":
    sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS)
    browser = Browser()
    if len(sys.argv) > 1:
        browser.new_tab(URL(sys.argv[1]))
    else:
        browser.new_tab(URL("https://browser.engineering/"))
    browser.raster_and_draw()
    mainloop(browser)
