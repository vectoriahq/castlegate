"""install/common/check.py's exit codes, against small local servers, in plain Python."""

import contextlib
import http.server
import importlib.util
import io
import pathlib
import socket
import threading
import unittest
import unittest.mock

_path = pathlib.Path(__file__).resolve().parents[2] / "install" / "common" / "check.py"
_spec = importlib.util.spec_from_file_location("castlegate_check", _path)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("Authorization"):
            # Review finding on #4: a dropped connection after the first request ended in a traceback.
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"signedIn": false}')


@contextlib.contextmanager
def server():
    s = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/ows/" % s.server_address[1]
    finally:
        s.shutdown()
        s.server_close()


@contextlib.contextmanager
def raw_server(reply):
    """A server that sends `reply` as bytes to every connection and closes it, HTTP or not."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                conn.sendall(reply)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d/ows/" % listener.getsockname()[1]
    finally:
        stop.set()
        listener.close()


def run(argv, env=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), unittest.mock.patch.dict("os.environ", env or {}, clear=False):
        code = check.main(argv)
    return code, out.getvalue()


class ExitCodes(unittest.TestCase):
    def test_loaded(self):
        with server() as url:
            code, out = run([url, "--map", "/p.qgs"], {"CASTLEGATE_TOKEN": ""})
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  Castlegate is loaded", out)

    def test_a_server_that_is_not_there(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        code, out = run(["http://127.0.0.1:%d/ows/" % port, "--map", "/p.qgs"])
        self.assertEqual(code, 2, out)
        self.assertIn("FAIL  could not reach", out)

    def test_a_connection_dropped_after_the_first_request(self):
        with server() as url:
            code, out = run([url, "--map", "/p.qgs"], {"CASTLEGATE_TOKEN": "x.y.z"})
        self.assertEqual(code, 2, out)
        self.assertIn("PASS  Castlegate is loaded", out)
        self.assertIn("FAIL  could not reach", out)

    def test_answers_that_never_arrive_whole(self):
        # Review finding on #4: http.client's own errors escaped request() with a traceback.
        replies = {
            "not HTTP": b"this is not HTTP\r\n\r\n",
            "a header line too long": b"HTTP/1.1 401 Unauthorized\r\nX: " + b"a" * 70000 + b"\r\n\r\n",
            "a body cut short": b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort",
            "a refusal's body cut short": b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Bearer\r\nContent-Length: 100\r\n\r\nshort",
        }
        for name, reply in replies.items():
            with raw_server(reply) as url:
                code, out = run([url, "--map", "/p.qgs"])
            self.assertEqual(code, 2, "%s: %s" % (name, out))
            self.assertIn("FAIL  could not reach", out, name)


if __name__ == "__main__":
    unittest.main()
