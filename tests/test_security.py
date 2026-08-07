import socketserver
import threading
import unittest

from browser import COOKIE_JAR, Tab, URL


class CookieHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.makefile("rwb")
        request_line = request.readline().decode("utf-8").strip()
        method, path, version = request_line.split(" ", 2)
        headers = {}
        while True:
            line = request.readline().decode("utf-8")
            if line in ("\r\n", "\n", ""):
                break
            name, value = line.split(":", 1)
            headers[name.casefold()] = value.strip()
        self.server.seen.append((method, path, headers))

        body = "ok"
        response = (
            "HTTP/1.0 200 OK\r\n"
            "Content-Length: 2\r\n"
        )
        if len(self.server.seen) == 1:
            response += "Set-Cookie: token=abc; SameSite=Lax\r\n"
        response += "\r\n"
        request.write(response.encode("utf-8") + body.encode("utf-8"))
        request.flush()


class TestServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, CookieHandler)
        self.seen = []


class SecurityTests(unittest.TestCase):
    def test_cookie_jar_and_samesite_lax(self):
        COOKIE_JAR.clear()
        with TestServer(("127.0.0.1", 0)) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            target = URL(f"http://127.0.0.1:{port}/")
            same_site_referrer = URL(f"http://127.0.0.1:{port}/login")
            cross_site_referrer = URL("http://attacker.example/")

            _, body = target.request()
            self.assertEqual(body, "ok")
            self.assertEqual(COOKIE_JAR["127.0.0.1"][0], "token=abc")

            target.request(same_site_referrer)
            target.request(cross_site_referrer, payload="action=add")

            self.assertEqual(httpd.seen[1][2]["cookie"], "token=abc")
            self.assertNotIn("cookie", httpd.seen[2][2])
            httpd.shutdown()

    def test_allowed_request_uses_origin(self):
        tab = Tab(600)
        tab.allowed_origins = ["http://example.com:80"]

        self.assertTrue(tab.allowed_request(URL("http://example.com/")))
        self.assertFalse(tab.allowed_request(URL("https://example.com/")))
        self.assertFalse(tab.allowed_request(URL("http://other.example/")))


if __name__ == "__main__":
    unittest.main()
