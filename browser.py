import socket
import ssl
import sys
import tkinter
import tkinter.font
import urllib.parse

import dukpy

# --- 1. 网络请求模块 ---
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

    def request(self, payload=None):
        if self.scheme == "about":
            return ""
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
                return ""
            
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
                    new_url = URL(location)
                    return new_url.request()
                return ""

            response_headers = {}
            while True:
                line = response.readline()
                if line == "\r\n":
                    break
                header, value = line.split(":", 1)
                response_headers[header.casefold()] = value.strip()

            assert "transfer-encoding" not in response_headers
            assert "content-encoding" not in response_headers

            body = response.read()
            s.close()
            return body
        except Exception as e:
            print(f"DEBUG: Request error: {e}")
            return ""

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

    def __repr__(self):
        return repr(self.text)

class Element:
    def __init__(self, tag, attributes, parent):
        self.tag = tag
        self.attributes = attributes
        self.children = []
        self.parent = parent
        self.style = {}
        self.is_focused = False

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
        while self.i < len(self.s):
            if self.s[self.i].isalnum() or self.s[self.i] in "#-.%":
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

    def pair(self):
        prop = self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()
        val = self.word()
        return prop.casefold(), val

    def body(self):
        pairs = {}
        while self.i < len(self.s) and self.s[self.i] != "}":
            try:
                self.whitespace()
                prop, val = self.pair()
                pairs[prop] = val
                self.whitespace()
                if self.i < len(self.s) and self.s[self.i] == ";":
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
        out = TagSelector(self.word().casefold())
        self.whitespace()
        while self.i < len(self.s) and self.s[self.i] != "{":
            tag = self.word()
            descendant = TagSelector(tag.casefold())
            out = DescendantSelector(out, descendant)
            self.whitespace()
        return out

    def parse(self):
        rules = []
        while self.i < len(self.s):
            try:
                self.whitespace()
                selector = self.selector()
                self.literal("{")
                self.whitespace()
                body = self.body()
                self.literal("}")
                rules.append((selector, body))
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

# --- 5. 样式应用与级联 ---
INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "color": "black",
}

def style(node, rules):
    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property] = node.parent.style[property]
        else:
            node.style[property] = default_value

    for selector, body in rules:
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

    for child in node.children:
        style(child, rules)

def cascade_priority(rule):
    selector, body = rule
    return selector.priority

# --- 6. 布局模块 ---
WIDTH, HEIGHT = 800, 600
HSTEP, VSTEP = 13, 18
SCROLL_STEP = 100
INPUT_WIDTH_PX = 200

FONTS = {}

def get_font(size, weight, style):
    key = (size, weight, style)
    if key not in FONTS:
        font = tkinter.font.Font(size=size, weight=weight, slant=style)
        label = tkinter.Label(font=font)
        FONTS[key] = (font, label)
    return FONTS[key][0]

class Rect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def contains_point(self, x, y):
        return x >= self.left and x < self.right and y >= self.top and y < self.bottom

BLOCK_ELEMENTS = [
    "html", "body", "article", "section", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
    "footer", "address", "p", "hr", "pre", "blockquote",
    "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
    "figcaption", "main", "div", "table", "form", "fieldset",
    "legend", "details", "summary"
]

class DocumentLayout:
    def __init__(self, node):
        self.node = node
        self.parent = None
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = 0

    def layout(self):
        self.width = WIDTH - 2 * HSTEP
        self.x = HSTEP
        self.y = VSTEP
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout(depth=0)
        self.height = child.height if child.height is not None else 0

    def paint(self):
        return []

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
                if isinstance(child, Element) and child.tag in BLOCK_ELEMENTS:
                    if child == self.node:
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
        size = int(float(node.style["font-size"][:-2]) * .75)
        color = node.style["color"]
        font = get_font(size, weight, style)
        w = font.measure(word)
        if self.cursor_x + w > self.width:
            self.new_line()
        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        text = TextLayout(node, word, line, previous_word)
        line.children.append(text)
        self.cursor_x += w + font.measure(" ")

    def input(self, node):
        weight = node.style["font-weight"]
        style = "roman" if node.style["font-style"] == "normal" else "italic"
        size = int(float(node.style["font-size"][:-2]) * .75)
        font = get_font(size, weight, style)
        w = INPUT_WIDTH_PX
        if node.tag == "button":
            text = input_text(node)
            w = font.measure(text) + 2 * HSTEP
        if self.cursor_x + w > self.width:
            self.new_line()
        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None
        layout = InputLayout(node, line, previous_word)
        line.children.append(layout)
        self.cursor_x += w + font.measure(" ")

    def self_rect(self):
        return Rect(self.x, self.y, self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color", "transparent")
        if bgcolor != "transparent":
            cmds.append(DrawRect(self.self_rect(), bgcolor))
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

        max_ascent = max([word.font.metrics("ascent") for word in self.children])
        baseline = self.y + 1.25 * max_ascent
        for word in self.children:
            word.y = baseline - word.font.metrics("ascent")
        max_descent = max([word.font.metrics("descent") for word in self.children])
        self.height = 1.25 * (max_ascent + max_descent)

    def paint(self):
        return []

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

    def layout(self):
        weight = self.node.style["font-weight"]
        style = "roman" if self.node.style["font-style"] == "normal" else "italic"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)
        self.width = self.font.measure(self.word)

        if self.previous:
            space = self.previous.font.measure(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = self.font.metrics("linespace")

    def paint(self):
        color = self.node.style["color"]
        return [DrawText(self.x, self.y, self.word, self.font, color)]

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

    def layout(self):
        weight = self.node.style["font-weight"]
        style = "roman" if self.node.style["font-style"] == "normal" else "italic"
        size = int(float(self.node.style["font-size"][:-2]) * .75)
        self.font = get_font(size, weight, style)
        if self.node.tag == "button":
            self.width = self.font.measure(input_text(self.node)) + 2 * HSTEP
        else:
            self.width = INPUT_WIDTH_PX

        if self.previous:
            space = self.previous.font.measure(" ")
            self.x = self.previous.x + space + self.previous.width
        else:
            self.x = self.parent.x

        self.height = self.font.metrics("linespace")

    def self_rect(self):
        return Rect(self.x, self.y, self.x + self.width, self.y + self.height)

    def paint(self):
        cmds = []
        bgcolor = self.node.style.get("background-color", "transparent")
        if bgcolor != "transparent":
            cmds.append(DrawRect(self.self_rect(), bgcolor))
            cmds.append(DrawOutline(self.self_rect(), "black", 1))
        color = self.node.style["color"]
        text_x = self.x + HSTEP // 2
        text_y = self.y + max(0, (self.height - self.font.metrics("linespace")) // 2)
        cmds.append(DrawText(text_x, text_y, input_text(self.node), self.font, color))
        if self.node.is_focused:
            w = self.font.measure(input_text(self.node))
            cmds.append(DrawLine(
                text_x + w, text_y,
                text_x + w, text_y + self.font.metrics("linespace"),
                "red", 1
            ))
        return cmds

# --- 7. 绘制命令模块 ---
class DrawText:
    def __init__(self, x1, y1, text, font, color):
        self.rect = Rect(x1, y1, x1 + font.measure(text), y1 + font.metrics("linespace"))
        self.text = text
        self.font = font
        self.color = color

    def execute(self, scroll, canvas):
        canvas.create_text(
            self.rect.left, self.rect.top - scroll,
            text=self.text,
            font=self.font,
            anchor='nw',
            fill=self.color
        )

class DrawRect:
    def __init__(self, rect, color):
        self.rect = rect
        self.color = color

    def execute(self, scroll, canvas):
        canvas.create_rectangle(
            self.rect.left, self.rect.top - scroll,
            self.rect.right, self.rect.bottom - scroll,
            width=0,
            fill=self.color
        )

class DrawLine:
    def __init__(self, x1, y1, x2, y2, color, thickness):
        self.rect = Rect(x1, y1, x2, y2)
        self.color = color
        self.thickness = thickness

    def execute(self, scroll, canvas):
        canvas.create_line(
            self.rect.left, self.rect.top - scroll,
            self.rect.right, self.rect.bottom - scroll,
            fill=self.color,
            width=self.thickness
        )

class DrawOutline:
    def __init__(self, rect, color, thickness):
        self.rect = rect
        self.color = color
        self.thickness = thickness

    def execute(self, scroll, canvas):
        canvas.create_rectangle(
            self.rect.left, self.rect.top - scroll,
            self.rect.right, self.rect.bottom - scroll,
            outline=self.color,
            width=self.thickness
        )

def paint_tree(layout_object, display_list):
    display_list.extend(layout_object.paint())
    for child in layout_object.children:
        paint_tree(child, display_list)

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
Object.defineProperty(Node.prototype, "innerHTML", {
    set: function(s) {
        call_python("innerHTML_set", this.handle, s.toString());
    }
});
Node.prototype.addEventListener = function(type, listener) {
    if (!this.listeners) this.listeners = {};
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
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

function Event(type) {
    this.type = type;
    this.defaultPrevented = false;
}
Event.prototype.preventDefault = function() {
    this.defaultPrevented = true;
}

function dispatch_event(type, handle) {
    var node = get_node(handle);
    var event = new Event(type);
    if (node.listeners && node.listeners[type]) {
        for (var i = 0; i < node.listeners[type].length; i++) {
            node.listeners[type][i].call(node, event);
        }
    }
    return !event.defaultPrevented;
}
0;
"""

class JSContext:
    def __init__(self, tab):
        self.tab = tab
        self.interp = dukpy.JSInterpreter()
        self.node_to_handle = {}
        self.handle_to_node = {}
        self.interp.export_function("log", print)
        self.interp.export_function("querySelectorAll", self.querySelectorAll)
        self.interp.export_function("getAttribute", self.getAttribute)
        self.interp.export_function("innerHTML_set", self.innerHTML_set)
        self.interp.evaljs(RUNTIME_JS)

    def run(self, script, code):
        try:
            return self.interp.evaljs(code)
        except dukpy.JSRuntimeError as e:
            print(f"Script {script} crashed", e)

    def dispatch_event(self, type, elt):
        handle = self.get_handle(elt)
        try:
            return self.interp.evaljs(f"dispatch_event({type!r}, {handle})")
        except dukpy.JSRuntimeError as e:
            print("Script event crashed", e)
            return True

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

    def innerHTML_set(self, handle, s):
        elt = self.handle_to_node[handle]
        doc = HTMLParser("<html><body>" + s + "</body></html>").parse()
        body = next(
            node for node in tree_to_list(doc, [])
            if isinstance(node, Element) and node.tag == "body"
        )
        elt.children = body.children
        self.fix_parent_pointers(elt)
        self.tab.render()

    def fix_parent_pointers(self, node):
        for child in node.children:
            child.parent = node
            self.fix_parent_pointers(child)

# --- 8. 浏览器 Chrome ---
class Chrome:
    def __init__(self, browser):
        self.browser = browser
        self.font = get_font(20, "normal", "roman")
        self.font_height = self.font.metrics("linespace")
        self.padding = 5
        self.tabbar_top = 0
        self.tabbar_bottom = self.font_height + 2 * self.padding

        plus_width = self.font.measure("+") + 2 * self.padding
        self.newtab_rect = Rect(
            self.padding, self.padding,
            self.padding + plus_width,
            self.padding + self.font_height
        )

        back_width = self.font.measure("<") + 2 * self.padding
        self.urlbar_top = self.tabbar_bottom
        self.urlbar_bottom = self.urlbar_top + self.font_height + 2 * self.padding
        self.bottom = self.urlbar_bottom

        forward_width = self.font.measure(">") + 2 * self.padding
        bookmark_width = self.font.measure("*") + 2 * self.padding
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
        tab_width = self.font.measure("Tab X") + 2 * self.padding
        return Rect(
            tabs_start + tab_width * i, self.tabbar_top,
            tabs_start + tab_width * (i + 1), self.tabbar_bottom
        )

    def paint(self):
        cmds = []

        cmds.append(DrawRect(Rect(0, 0, WIDTH, self.bottom), "white"))
        cmds.append(DrawLine(0, self.bottom, WIDTH, self.bottom, "black", 1))

        cmds.append(DrawOutline(self.newtab_rect, "black", 1))
        cmds.append(DrawText(
            self.newtab_rect.left + self.padding,
            self.newtab_rect.top,
            "+", self.font, "black"
        ))

        for i, tab in enumerate(self.browser.tabs):
            bounds = self.tab_rect(i)
            cmds.append(DrawLine(bounds.left, 0, bounds.left, bounds.bottom, "black", 1))
            cmds.append(DrawLine(bounds.right, 0, bounds.right, bounds.bottom, "black", 1))
            cmds.append(DrawText(
                bounds.left + self.padding,
                bounds.top + self.padding,
                "Tab {}".format(i), self.font, "black"
            ))
            if tab == self.browser.active_tab:
                cmds.append(DrawLine(bounds.left, bounds.bottom, bounds.right, bounds.bottom, "black", 1))
                cmds.append(DrawLine(bounds.right, bounds.bottom, WIDTH, bounds.bottom, "black", 1))

        cmds.append(DrawOutline(self.back_rect, "black", 1))
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

        cmds.append(DrawOutline(self.bookmark_rect, "black", 1))
        cmds.append(DrawText(
            self.bookmark_rect.left + self.padding,
            self.bookmark_rect.top,
            "*", self.font, self.bookmark_color()
        ))

        cmds.append(DrawOutline(self.address_rect, "black", 1))
        if self.focus == "address bar":
            cmds.append(DrawText(
                self.address_rect.left + self.padding,
                self.address_rect.top,
                self.address_bar, self.font, "black"
            ))
            w = self.font.measure(self.address_bar)
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
                url, self.font, "black"
            ))

        return cmds

    def back_color(self):
        if self.browser.active_tab and self.browser.active_tab.can_go_back():
            return "black"
        return "gray"

    def forward_color(self):
        if self.browser.active_tab and self.browser.active_tab.can_go_forward():
            return "black"
        return "gray"

    def bookmark_color(self):
        if self.browser.is_current_page_bookmarked():
            return "gold"
        return "black"

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
                    self.browser.active_tab = tab
                    self.browser.set_title()
                    break

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
                self.browser.active_tab.load(url, bookmarks=self.browser.bookmarks)
                self.focus = None
                self.browser.set_title()
                self.browser.draw()
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
""").parse()

class Tab:
    def __init__(self, tab_height):
        self.tab_height = tab_height
        self.url = None
        self.scroll = 0
        self.history = []
        self.forward_history = []
        self.display_list = []
        self.document = None
        self.bookmarks = set()
        self.focus = None
        self.rules = DEFAULT_STYLE_SHEET.copy()
        self.js = None

    def load(self, url, add_history=True, bookmarks=None, payload=None):
        self.url = url
        if bookmarks is not None:
            self.bookmarks = bookmarks
        if add_history:
            self.history.append(url)
            self.forward_history = []
        
        try:
            body = self.about_page(url) if url.scheme == "about" else url.request(payload)
            if len(body) == 0:
                return
        except Exception as e:
            print(f"请求失败: {e}")
            return
        
        self.nodes = HTMLParser(body).parse()
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
            try:
                body = script_url.request()
            except:
                continue
            self.js.run(script, body)

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
                body = style_url.request()
                rules.extend(CSSParser(body).parse())
            except:
                continue

        self.rules = rules
        style(self.nodes, sorted(self.rules, key=cascade_priority))

        self.document = DocumentLayout(self.nodes)
        self.document.layout()

        self.display_list = []
        paint_tree(self.document, self.display_list)
        self.scroll = 0

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

    def draw(self, canvas, offset):
        for cmd in self.display_list:
            if cmd.rect.top > self.scroll + self.tab_height:
                continue
            if cmd.rect.bottom < self.scroll:
                continue
            cmd.execute(self.scroll - offset, canvas)

    def scrolldown(self):
        if not self.document:
            return
        max_y = max(0, self.document.height + 2 * VSTEP - self.tab_height)
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)

    def click(self, x, y):
        elt = self.hit_test(x, y)
        if not elt:
            self.blur()
            return
        while elt:
            if isinstance(elt, Text):
                pass
            elif elt.tag == "input":
                self.blur()
                self.focus = elt
                elt.is_focused = True
                if hasattr(self, "browser"):
                    self.browser.window.focus_force()
                    self.browser.canvas.focus_set()
                return
            elif elt.tag == "button":
                self.blur()
                if not self.js or self.js.dispatch_event("click", elt):
                    self.submit_form(elt)
                return
            elif elt.tag == "a" and "href" in elt.attributes:
                self.blur()
                if not self.js or self.js.dispatch_event("click", elt):
                    url = self.url.resolve(elt.attributes["href"])
                    self.load(url, bookmarks=self.bookmarks)
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
            and obj.x <= x < obj.x + obj.width
            and obj.y <= y < obj.y + obj.height
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
        if self.js and not self.js.dispatch_event("submit", elt):
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
        if self.focus:
            self.focus.attributes["value"] = self.focus.attributes.get("value", "") + char
            self.render()

    def backspace(self):
        if self.focus:
            self.focus.attributes["value"] = self.focus.attributes.get("value", "")[:-1]
            self.render()

    def enter(self):
        if self.focus:
            self.submit_form(self.focus)

    def blur(self):
        if self.focus:
            self.focus.is_focused = False
            self.focus = None

    def render(self):
        style(self.nodes, sorted(self.rules, key=cascade_priority))
        self.document = DocumentLayout(self.nodes)
        self.document.layout()
        self.display_list = []
        paint_tree(self.document, self.display_list)

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
        self.window = tkinter.Tk()
        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT,
            bg="white"
        )
        self.canvas.pack()
        self.canvas.focus_set()
        self.window.focus_force()

        self.tabs = []
        self.active_tab = None
        self.bookmarks = set()
        self.chrome = Chrome(self)

        self.window.bind("<Down>", self.handle_down)
        self.window.bind("<Button-1>", self.handle_click)
        self.window.bind("<Button-2>", self.handle_middle_click)
        self.window.bind("<Key>", self.handle_key)
        self.window.bind("<Return>", self.handle_enter)
        self.window.bind("<BackSpace>", self.handle_backspace)

    def handle_down(self, e):
        if self.active_tab:
            self.active_tab.scrolldown()
        self.draw()

    def handle_click(self, e):
        self.window.focus_force()
        self.canvas.focus_set()
        if e.y < self.chrome.bottom:
            self.chrome.click(e.x, e.y)
        else:
            if self.active_tab:
                self.active_tab.click(e.x, e.y - self.chrome.bottom)
                self.set_title()
        self.draw()

    def handle_middle_click(self, e):
        if e.y >= self.chrome.bottom and self.active_tab:
            url = self.active_tab.middle_click(e.x, e.y - self.chrome.bottom)
            if url:
                self.new_tab(url)
        self.draw()

    def handle_key(self, e):
        if len(e.char) > 0:
            if 0x20 <= ord(e.char) < 0x7f:
                if self.chrome.focus:
                    self.chrome.keypress(e.char)
                elif self.active_tab:
                    self.active_tab.keypress(e.char)
                self.draw()
                return
            if e.char in ['。', '．']:
                if self.chrome.focus:
                    self.chrome.keypress('.')
                elif self.active_tab:
                    self.active_tab.keypress('.')
                self.draw()
                return
        
        keysym_to_char = {
            ".": ".",
            "period": ".",
            "kana_fullstop": ".",
            "comma": ",",
            "minus": "-",
            "slash": "/",
            "semicolon": ";",
            "equal": "=",
            "bracketleft": "[",
            "bracketright": "]",
            "backslash": "\\",
            "quote": "'",
            "grave": "`",
        }
        if e.keysym in keysym_to_char:
            if self.chrome.focus:
                self.chrome.keypress(keysym_to_char[e.keysym])
            elif self.active_tab:
                self.active_tab.keypress(keysym_to_char[e.keysym])
            self.draw()

    def handle_backspace(self, e):
        if self.chrome.focus:
            self.chrome.backspace()
        elif self.active_tab:
            self.active_tab.backspace()
        self.draw()

    def handle_enter(self, e):
        if self.chrome.focus:
            self.chrome.enter()
        elif self.active_tab:
            self.active_tab.enter()
            self.set_title()
        self.draw()

    def new_tab(self, url):
        new_tab = Tab(HEIGHT - self.chrome.bottom)
        new_tab.load(url, bookmarks=self.bookmarks)
        self.active_tab = new_tab
        self.tabs.append(new_tab)
        self.set_title()
        self.draw()

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

    def set_title(self):
        if not self.active_tab or not self.active_tab.url:
            self.window.title("Browser")
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
        self.window.title(title)

    def draw(self):
        self.canvas.delete("all")
        if self.active_tab:
            self.active_tab.draw(self.canvas, self.chrome.bottom)
        for cmd in self.chrome.paint():
            cmd.execute(0, self.canvas)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = URL(sys.argv[1])
        browser = Browser()
        browser.new_tab(url)
        tkinter.mainloop()
    else:
        print("Usage: python3 browser.py <url>")
        print("Example: python3 browser.py http://example.org/")
