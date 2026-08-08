import base64
import socketserver
import threading
import time
import unittest

from browser import Element, HTMLParser, Tab, Text, URL, tree_to_list


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6vQAAAABJRU5ErkJggg=="
)
CROSS_CHILD_PORT = None


class EmbedHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.makefile("rb")
        request_line = request.readline().decode("utf-8").strip()
        if not request_line:
            return
        method, path, version = request_line.split(" ", 2)
        while True:
            line = request.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if path == "/img.png":
            body = PNG
            content_type = "image/png"
        elif path == "/frame.html":
            body = b"<html><body><p>frame</p></body></html>"
            content_type = "text/html; charset=utf-8"
        elif path == "/pm-parent.js":
            body = (
                b"window.msg='';"
                b"window.addEventListener('message', function(e) {"
                b"window.msg = e.data; });"
            )
            content_type = "text/javascript; charset=utf-8"
        elif path == "/pm-child.js":
            body = b"window.parent.postMessage('hello', '*');"
            content_type = "text/javascript; charset=utf-8"
        elif path == "/same-origin-parent.js":
            body = (
                b"window.result = 'unset';"
                b"window.addEventListener('message', function(e) {"
                b"window.result = document.querySelectorAll('div')[0].getAttribute('data-msg');"
                b"});"
            )
            content_type = "text/javascript; charset=utf-8"
        elif path == "/same-origin-child.js":
            body = (
                b"window.parent.document.querySelectorAll('div')[0]"
                b".setAttribute('data-msg', 'updated');"
                b"window.parent.postMessage('done', '*');"
            )
            content_type = "text/javascript; charset=utf-8"
        elif path == "/same-origin-child.html":
            body = (
                b"<html><body>"
                b"<script src='/same-origin-child.js'></script>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"
        elif path == "/same-origin.html":
            body = (
                b"<html><body>"
                b"<div data-msg='before'></div>"
                b"<iframe src='/same-origin-child.html'></iframe>"
                b"<script src='/same-origin-parent.js'></script>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"
        elif path == "/pm-child.html":
            body = (
                b"<html><body>"
                b"<script src='/pm-child.js'></script>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"
        elif path == "/post-message.html":
            body = (
                b"<html><body>"
                b"<iframe src='/pm-child.html'></iframe>"
                b"<script src='/pm-parent.js'></script>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"
        elif path == "/cross-origin.html":
            body = (
                "<html><body>"
                f"<iframe src='http://127.0.0.1:{CROSS_CHILD_PORT}/child.html'></iframe>"
                "<script src='/pm-parent.js'></script>"
                "</body></html>"
            ).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = (
                b"<html><body>"
                b"<img src='/img.png' width='1' height='1'>"
                b"<iframe src='/frame.html' width='120' height='60'></iframe>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"

        response = (
            f"HTTP/1.0 200 OK\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("utf-8") + body
        self.request.sendall(response)


class TestServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CrossOriginHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.makefile("rb")
        request_line = request.readline().decode("utf-8").strip()
        if not request_line:
            return
        method, path, version = request_line.split(" ", 2)
        while True:
            line = request.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if path == "/child.js":
            body = (
                b"try {"
                b"  window.parent.document.querySelectorAll('div')[0];"
                b"} catch (e) {"
                b"  window.parent.postMessage('blocked', '*');"
                b"}"
            )
            content_type = "text/javascript; charset=utf-8"
        else:
            body = (
                b"<html><body>"
                b"<script src='/child.js'></script>"
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"

        response = (
            f"HTTP/1.0 200 OK\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("utf-8") + body
        self.request.sendall(response)


class EmbedTests(unittest.TestCase):
    def run_until(self, tab, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ran = False
            while tab.task_runner.run_one():
                ran = True
            if predicate():
                return True
            if not ran:
                time.sleep(0.02)
        return predicate()

    def test_image_and_iframe_load(self):
        with TestServer(("127.0.0.1", 0), EmbedHandler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]

            tab = Tab(500)
            tab.load(URL(f"http://127.0.0.1:{port}/"))
            self.assertTrue(self.run_until(
                tab,
                lambda: any(
                    isinstance(node, Element)
                    and node.tag == "img"
                    and node.image
                    for node in tree_to_list(tab.nodes, [])
                )
                and any(
                    isinstance(node, Element)
                    and node.tag == "iframe"
                    and node.frame
                    and node.frame.loaded
                    for node in tree_to_list(tab.nodes, [])
                ),
            ))
            tab.render()

            images = [
                node for node in tree_to_list(tab.nodes, [])
                if isinstance(node, Element) and node.tag == "img"
            ]
            iframes = [
                node for node in tree_to_list(tab.nodes, [])
                if isinstance(node, Element) and node.tag == "iframe"
            ]

            self.assertTrue(images[0].image)
            self.assertTrue(iframes[0].frame)
            self.assertTrue(iframes[0].frame.loaded)
            self.assertIsNotNone(iframes[0].frame.document)

            httpd.shutdown()

    def test_same_origin_post_message_reaches_parent_window(self):
        with TestServer(("127.0.0.1", 0), EmbedHandler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]

            tab = Tab(500)
            tab.load(URL(f"http://127.0.0.1:{port}/post-message.html"))
            self.assertTrue(self.run_until(
                tab,
                lambda: tab.js.interp.evaljs(
                    tab.js.wrap("window.msg", tab.window_id)
                ) == "hello",
            ))

            message = tab.js.interp.evaljs(
                tab.js.wrap("window.msg", tab.window_id)
            )
            self.assertEqual(message, "hello")

            httpd.shutdown()

    def test_same_origin_parent_dom_access_works(self):
        with TestServer(("127.0.0.1", 0), EmbedHandler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]

            tab = Tab(500)
            tab.load(URL(f"http://127.0.0.1:{port}/same-origin.html"))
            self.assertTrue(self.run_until(
                tab,
                lambda: tab.js.interp.evaljs(
                    tab.js.wrap("window.result", tab.window_id)
                ) == "updated",
            ))

            result = tab.js.interp.evaljs(
                tab.js.wrap("window.result", tab.window_id)
            )
            self.assertEqual(result, "updated")

            httpd.shutdown()

    def test_cross_origin_access_is_blocked_but_post_message_works(self):
        with TestServer(("127.0.0.1", 0), EmbedHandler) as parent_httpd:
            with TestServer(("127.0.0.1", 0), CrossOriginHandler) as child_httpd:
                parent_thread = threading.Thread(
                    target=parent_httpd.serve_forever, daemon=True
                )
                child_thread = threading.Thread(
                    target=child_httpd.serve_forever, daemon=True
                )
                parent_thread.start()
                child_thread.start()
                parent_port = parent_httpd.server_address[1]
                child_port = child_httpd.server_address[1]
                global CROSS_CHILD_PORT
                CROSS_CHILD_PORT = child_port

                tab = Tab(500)
                tab.load(URL(f"http://127.0.0.1:{parent_port}/cross-origin.html"))
                self.assertTrue(self.run_until(
                    tab,
                    lambda: tab.js.interp.evaljs(
                        tab.js.wrap("window.msg", tab.window_id)
                    ) == "blocked",
                ))

                message = tab.js.interp.evaljs(
                    tab.js.wrap("window.msg", tab.window_id)
                )
                self.assertEqual(message, "blocked")

                CROSS_CHILD_PORT = None
                child_httpd.shutdown()
                parent_httpd.shutdown()

    def test_contenteditable_updates_text_and_reuses_document_layout(self):
        tab = Tab(300)
        tab.nodes = HTMLParser(
            "<html><body><div contenteditable='true'>edit</div>"
            "<p>stable</p></body></html>"
        ).parse()
        tab.render()
        editable = next(
            node for node in tree_to_list(tab.nodes, [])
            if isinstance(node, Element) and node.tag == "div"
        )
        document = tab.document
        root_layout = document.children[0]

        tab.focus_element(editable)
        tab.keypress("!")
        tab.run_animation_frame()

        text_nodes = [
            node for node in tree_to_list(editable, [])
            if isinstance(node, Text)
        ]
        self.assertEqual(text_nodes[-1].text, "edit!")
        self.assertIs(tab.document, document)
        self.assertIs(tab.document.children[0], root_layout)

        tab.render()
        self.assertIs(tab.document, document)
        self.assertIs(tab.document.children[0], root_layout)



if __name__ == "__main__":
    unittest.main()
