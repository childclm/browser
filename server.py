import socket
import threading
import urllib.parse

HOST = "127.0.0.1"
PORT = 8000

ENTRIES = []


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


def show_comments():
    out = "<!doctype html><html><head><meta charset='utf-8'>"
    out += "<title>Guest Book</title><script src='/comment.js'></script></head><body>"
    out += "<h1>Guest Book</h1>"
    out += "<p id='warning'></p>"
    out += """
    <form action="/add" method="post">
      <p><input name="guest" value=""></p>
      <p><button>Submit</button></p>
    </form>
    """
    out += "<hr>"
    for entry in ENTRIES:
        out += f"<p>{entry}</p>"
    out += "</body></html>"
    return out


def comment_js():
    return """
var inputs = document.querySelectorAll("input");
var forms = document.querySelectorAll("form");
var warnings = document.querySelectorAll("p");

forms[0].addEventListener("submit", function(e) {
    var comment = inputs[0].getAttribute("value");
    if (comment.length > 100) {
        warnings[0].innerHTML = "Comment too long";
        e.preventDefault();
    }
});
"""


def add_entry(params):
    if "guest" in params and params["guest"].strip() and len(params["guest"]) <= 100:
        ENTRIES.append(params["guest"])
    return show_comments()


def not_found(url, method):
    return f"<!doctype html><h1>{method} {url} not found!</h1>"


def do_request(method, url, headers, body):
    path = url.split("?", 1)[0]
    if method == "GET" and path == "/":
        return "200 OK", show_comments()
    if method == "GET" and path == "/comment.js":
        return "200 OK", comment_js()
    if method == "POST" and path in ["/add", "/submit"]:
        params = form_decode(body)
        return "200 OK", add_entry(params)
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

        status, response_body = do_request(method, url, headers, body)
        body_bytes = response_body.encode("utf-8")
        response = [
            f"HTTP/1.0 {status}",
            "Content-Type: text/html; charset=utf-8",
            f"Content-Length: {len(body_bytes)}",
            "",
            "",
        ]
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
