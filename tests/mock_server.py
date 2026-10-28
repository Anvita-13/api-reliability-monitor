"""A tiny HTTP server for testing the monitor: endpoints that can pass, fail, hang or be slow.

Run it by hand for demos:

    python tests/mock_server.py --port 9100

    GET  /ok               always 200
    GET  /status/<code>    answers with that status code
    GET  /slow/<ms>        waits <ms> milliseconds, then 200
    GET  /redirect         302 to /ok
    GET  /flaky            200 normally, 500 while the server is "down"
    POST /__mode?value=down|up|hang     flip /flaky (hang = never answers in time)
    GET  /__mode           show the current mode

The tests start it on a random port and flip the mode to force failures and recoveries.
"""
import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):  # keep test output quiet
        pass

    def _reply(self, code: int, body: str = ""):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        if code == 302:
            self.send_header("Location", "/ok")
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except ConnectionError:
            pass  # the client gave up (e.g. a timeout test); nothing to report

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        server: MockServer = self.server.owner
        if path == "/__mode":
            if self.command == "POST":
                value = parse_qs(parts.query).get("value", ["up"])[0]
                if value in ("up", "down", "hang"):
                    server.mode = value
            return self._reply(200, server.mode)
        server.hits[path] = server.hits.get(path, 0) + 1
        if path == "/ok":
            return self._reply(200, "ok")
        if path.startswith("/status/"):
            try:
                return self._reply(int(path.rsplit("/", 1)[1]), "status")
            except ValueError:
                return self._reply(400, "bad status")
        if path.startswith("/slow/"):
            time.sleep(int(path.rsplit("/", 1)[1]) / 1000.0)
            return self._reply(200, "slow")
        if path == "/redirect":
            return self._reply(302)
        if path == "/flaky":
            if server.mode == "hang":
                time.sleep(server.hang_seconds)
                return self._reply(200, "late")
            return self._reply(500 if server.mode == "down" else 200, server.mode)
        return self._reply(404, "not found")


class MockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, hang_seconds: float = 3.0):
        self.mode = "up"
        self.hang_seconds = hang_seconds
        self.hits: dict[str, int] = {}
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.owner = self
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "MockServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path: str = "/ok") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    args = parser.parse_args()
    server = MockServer(host=args.host, port=args.port).start()
    print(f"Mock API listening on http://{args.host}:{server.port}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
