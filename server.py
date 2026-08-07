import socket
import threading
import urllib.parse
import html
import random

HOST = "127.0.0.1"
PORT = 8000

ENTRIES = []
SESSIONS = {}
LOGINS = {
    "crashoverride": "0cool",
    "cerealkiller": "emmanuel",
}


def form_decode(body):
    params = {}
    for field in body.split("&"):
        if not field:
            continue
        if "=" in field:
            name, value = field.split("=", 1)
        else:
            name, value = field, ""
        name = urllib.parse.unquote_plus(name)
        value = urllib.parse.unquote_plus(value)
        params[name] = value
    return params


def show_comments(session):
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Guest Book</title></head><body>"
    if "user" in session:
        out += f"<h1>Hello, {html.escape(session['user'])}</h1>"
        nonce = str(random.random())[2:]
        session["nonce"] = nonce
        out += "<form action='/add' method='post'>"
        out += "<p><input name='guest' value=''></p>"
        out += f"<input name='nonce' type='hidden' value='{nonce}'>"
        out += "<p><button>Sign the book!</button></p>"
        out += "</form>"
    else:
        out += "<a href='/login'>Sign in to write in the guest book</a>"
    for entry, who in ENTRIES:
        out += "<p>" + html.escape(entry)
        out += " <i>by " + html.escape(who) + "</i></p>"
    out += "<link rel='stylesheet' href='/comment.css'>"
    out += "<strong></strong>"
    out += "<script src='/comment.js'></script>"
    out += "<script src='https://example.com/evil.js'></script>"
    out += "</body></html>"
    return out


def comment_js():
    return """
var allow_submit = true;
var strong = document.querySelectorAll("strong")[0];
var inputs = document.querySelectorAll("input");
var forms = document.querySelectorAll("form");

function lengthCheck() {
    var comment = this.getAttribute("value");
    allow_submit = comment.length <= 100;
    if (!allow_submit) {
        strong.innerHTML = "Comment too long!";
    } else {
        strong.innerHTML = "";
    }
}

for (var i = 0; i < inputs.length; i++) {
    inputs[i].addEventListener("keydown", lengthCheck);
}

forms[0].addEventListener("submit", function(e) {
    if (!allow_submit) {
        e.preventDefault();
    }
});
"""


def comment_css():
    return "strong { font-weight: bold; color: red; }"


def login_form(session):
    out = "<!doctype html><html><body>"
    out += "<h1>Sign in</h1>"
    out += "<form action='/' method='post'>"
    out += "<p>Username: <input name='username' value=''></p>"
    out += "<p>Password: <input name='password' value=''></p>"
    out += "<p><button>Log in</button></p>"
    out += "</form></body></html>"
    return out


def do_login(session, params):
    username = params.get("username")
    password = params.get("password")
    if username in LOGINS and LOGINS[username] == password:
        session["user"] = username
        return "200 OK", show_comments(session)
    return "401 Unauthorized", (
        "<!doctype html><h1>Invalid username or password</h1>"
    )


def show_script_demo():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Script Demo</title></head><body>"
    out += "<h1>Script Demo</h1>"
    out += "<p>Load status: <strong>not run</strong></p>"
    out += "<p>Button status: <strong>not clicked</strong></p>"
    out += "<p>Link status: <strong>not blocked</strong></p>"
    out += "<p>Input status: <strong>waiting</strong></p>"
    out += "<p><button>Click JS button</button></p>"
    out += "<p><a href='/script-failed'>Blocked link</a></p>"
    out += "<p><input name='demo' value=''></p>"
    out += "<script src='/script-demo.js'></script>"
    out += "</body></html>"
    return out


def show_visual_demo():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Visual Effects Demo</title></head><body>"
    out += "<h1>Visual Effects Demo</h1>"
    out += (
        "<div style='background-color:orange'>"
        "<p style='color:#00000080'>50% transparent text over orange</p>"
        "</div>"
    )
    out += (
        "<div style='background-color:lightblue;opacity:.5'>"
        "<p>Whole stacking context at 50% opacity</p>"
        "</div>"
    )
    out += (
        "<div style='background-color:orange'>"
        "<p style='background-color:lightblue;mix-blend-mode:multiply'>"
        "multiply blend mode"
        "</p></div>"
    )
    out += (
        "<div style='border-radius:30px;background-color:lightblue;overflow:clip'>"
        "This test text exists here to ensure that the div element is large "
        "enough that the border radius and clipping are obvious."
        "</div>"
    )
    out += "</body></html>"
    return out


def show_scheduling_demo():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Scheduling Demo</title></head><body>"
    out += "<h1>Scheduling Demo</h1>"
    out += "<p>setTimeout: <strong>waiting</strong></p>"
    out += "<p>requestAnimationFrame: <strong>waiting</strong></p>"
    out += "<p>async XMLHttpRequest: <strong>waiting</strong></p>"
    out += "<p>Event loop order: <strong>page script queued</strong></p>"
    out += "<script src='/scheduling-demo.js'></script>"
    out += "</body></html>"
    return out


def scheduling_demo_js():
    return """
var strongs = document.querySelectorAll("strong");
var frames = 0;

strongs[3].innerHTML = "script executed as a task";

setTimeout(function() {
    strongs[0].innerHTML = "callback ran after 250ms";
}, 250);

function tick() {
    frames = frames + 1;
    strongs[1].innerHTML = "frame " + frames;
    if (frames < 90) {
        requestAnimationFrame(tick);
    } else {
        strongs[1].innerHTML = "animation complete";
    }
}
requestAnimationFrame(tick);

var xhr = new XMLHttpRequest();
xhr.open("GET", "/scheduling-data", true);
xhr.onload = function() {
    strongs[2].innerHTML = this.responseText;
};
xhr.send();
"""


def show_animation_demo():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Animation Demo</title></head><body>"
    out += "<h1>Animation Demo</h1>"
    out += "<p>CSS transition: <strong>ready</strong></p>"
    out += "<p><button>Animate box</button></p>"
    out += (
        "<div style='width:260px;height:70px;background-color:lightblue;"
        "opacity:0.15;transition:opacity 0.6s, transform 0.6s;"
        "transform:translate(0px, 0px)'>"
        "This box fades and moves"
        "</div>"
    )
    out += "<script src='/animation-demo.js'></script>"
    out += "</body></html>"
    return out


def show_accessibility_demo():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Accessibility Demo</title></head><body>"
    out += "<h1>Accessibility Demo</h1>"
    out += "<p>Use Tab to move through the controls and Enter to activate them.</p>"
    out += "<p><button tabindex='1'>Announce status</button></p>"
    out += "<p><a tabindex='2' href='/accessibility-demo'>Demo link</a></p>"
    out += (
        "<p><input aria-label='Your name' name='name' value=''></p>"
    )
    out += (
        "<div tabindex='3' aria-label='Focusable status panel'>"
        "A focusable panel with a custom label.</div>"
    )
    out += "<p tabindex='-1'>This paragraph is skipped by Tab.</p>"
    out += "<p aria-hidden='true'>This text is hidden from the accessibility tree.</p>"
    out += "<p>Alert: <strong role='alert'>Waiting for a status update</strong></p>"
    out += "<p>Status: <strong>ready</strong></p>"
    out += "<script src='/accessibility-demo.js'></script>"
    out += "</body></html>"
    return out


def accessibility_demo_js():
    return """
var strongs = document.querySelectorAll("strong");
var button = document.querySelectorAll("button")[0];
var alert = strongs[0];
var status = strongs[1];

button.addEventListener("click", function(e) {
    alert.innerHTML = "Button activated";
    status.innerHTML = "button click handled";
    e.preventDefault();
});

setTimeout(function() {
    alert.innerHTML = "Asynchronous update received";
    status.innerHTML = "alert updated by setTimeout";
}, 600);
"""


def animation_demo_js():
    return """
var box = document.querySelectorAll("div")[0];
var status = document.querySelectorAll("strong")[0];
var button = document.querySelectorAll("button")[0];
var visible = false;

button.addEventListener("click", function(e) {
    visible = !visible;
    if (visible) {
        box.style = "opacity:0.999;transform:translate(260px, 0px);"
            + "transition:opacity 0.6s, transform 0.6s;"
            + "background-color:lightgreen";
        status.innerHTML = "running";
    } else {
        box.style = "opacity:0.15;transform:translate(0px, 0px);"
            + "transition:opacity 0.6s, transform 0.6s;"
            + "background-color:lightblue";
        status.innerHTML = "returning";
    }
});
"""


def script_demo_js():
    return """
var strongs = document.querySelectorAll("strong");
var buttons = document.querySelectorAll("button");
var links = document.querySelectorAll("a");
var inputs = document.querySelectorAll("input");

strongs[0].innerHTML = "script loaded";

buttons[0].addEventListener("click", function(e) {
    strongs[1].innerHTML = "button handled by JavaScript";
    e.preventDefault();
});

links[0].addEventListener("click", function(e) {
    strongs[2].innerHTML = "navigation blocked by JavaScript";
    e.preventDefault();
});

inputs[0].addEventListener("keydown", function(e) {
    var value = this.getAttribute("value");
    strongs[3].innerHTML = "keydown handled; length " + value.length;
});
"""


def add_entry(session, params):
    if "user" not in session:
        return
    if session.get("nonce") != params.get("nonce"):
        return
    if "guest" in params and params["guest"].strip() and len(params["guest"]) <= 100:
        ENTRIES.append((params["guest"], session["user"]))


def not_found(url, method):
    return f"<!doctype html><h1>{method} {url} not found!</h1>"


def do_request(session, method, url, headers, body):
    path = url.split("?", 1)[0]
    if method == "GET" and path == "/":
        return "200 OK", show_comments(session)
    if method == "GET" and path == "/login":
        return "200 OK", login_form(session)
    if method == "GET" and path == "/script-demo":
        return "200 OK", show_script_demo()
    if method == "GET" and path == "/visual-demo":
        return "200 OK", show_visual_demo()
    if method == "GET" and path == "/scheduling-demo":
        return "200 OK", show_scheduling_demo()
    if method == "GET" and path == "/animation-demo":
        return "200 OK", show_animation_demo()
    if method == "GET" and path == "/accessibility-demo":
        return "200 OK", show_accessibility_demo()
    if method == "GET" and path == "/script-demo.js":
        return "200 OK", script_demo_js()
    if method == "GET" and path == "/scheduling-demo.js":
        return "200 OK", scheduling_demo_js()
    if method == "GET" and path == "/scheduling-data":
        return "200 OK", "async response received"
    if method == "GET" and path == "/animation-demo.js":
        return "200 OK", animation_demo_js()
    if method == "GET" and path == "/accessibility-demo.js":
        return "200 OK", accessibility_demo_js()
    if method == "GET" and path == "/script-failed":
        return "200 OK", "<!doctype html><h1>JavaScript did not block navigation</h1>"
    if method == "GET" and path == "/comment.js":
        return "200 OK", comment_js()
    if method == "GET" and path == "/comment.css":
        return "200 OK", comment_css()
    if method == "POST" and path == "/add":
        params = form_decode(body)
        add_entry(session, params)
        return "200 OK", show_comments(session)
    if method == "POST" and path == "/":
        params = form_decode(body)
        return do_login(session, params)
    return "404 Not Found", not_found(url, method)


def handle_connection(conx):
    try:
        req = conx.makefile("rwb")
        request_line = req.readline().decode("utf-8").strip()
        if not request_line:
            return
        method, url, version = request_line.split(" ", 2)
        headers = {}
        while True:
            line = req.readline().decode("utf-8")
            if line in ("\r\n", "\n", ""):
                break
            name, value = line.split(":", 1)
            headers[name.casefold()] = value.strip()
        body = ""
        if "content-length" in headers:
            length = int(headers["content-length"])
            body = req.read(length).decode("utf-8")

        if "cookie" in headers and headers["cookie"].startswith("token="):
            token = headers["cookie"][len("token="):].split(";", 1)[0]
        else:
            token = str(random.random())[2:]
        session = SESSIONS.setdefault(token, {})

        status, response_body = do_request(session, method, url, headers, body)
        body_bytes = response_body.encode("utf-8")
        response = [
            f"HTTP/1.0 {status}",
            "Content-Type: text/html; charset=utf-8",
            f"Content-Length: {len(body_bytes)}",
        ]
        if "cookie" not in headers:
            response.append(f"Set-Cookie: token={token}; SameSite=Lax")
        response.append(
            f"Content-Security-Policy: default-src "
            f"http://{HOST}:{conx.getsockname()[1]}"
        )
        response.extend(["", ""])
        req.write("\r\n".join(response).encode("utf-8") + body_bytes)
        req.flush()
    finally:
        conx.close()


def serve(host=HOST, port=PORT):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen()
    print(f"Listening on http://{host}:{port}/")
    while True:
        conx, _ = server.accept()
        threading.Thread(target=handle_connection, args=(conx,), daemon=True).start()


if __name__ == "__main__":
    serve()
