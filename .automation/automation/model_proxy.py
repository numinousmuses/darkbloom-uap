"""A per-run, bounded model connection for network-isolated containers."""

import contextlib
import http.client
import http.server
import json
import os
import socket
import socketserver
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@contextlib.contextmanager
def gateway(path, model, max_requests=100):
    key = os.environ["ANTHROPIC_API_KEY"]
    lock = threading.Lock()
    requests = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal requests
            try:
                size = int(self.headers.get("Content-Length", "0"))
                path = urlsplit(self.path).path
                if path not in ("/v1/messages", "/v1/messages/count_tokens"):
                    self.send_error(404)
                    return
                if not 0 < size <= 8_000_000:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                data = json.loads(body)
                if data.get("model") != model or data.get("max_tokens", 1) > 64000:
                    self.send_error(403)
                    return
                with lock:
                    requests += 1
                    admitted = requests <= max_requests
                if not admitted:
                    self.send_error(429)
                    return
                headers = {"Content-Type": "application/json", "x-api-key": key,
                           "anthropic-version": "2023-06-01"}
                if self.headers.get("anthropic-beta"):
                    headers["anthropic-beta"] = self.headers["anthropic-beta"]
                request = urllib.request.Request("https://api.anthropic.com" + path,
                                                 body, headers, method="POST")
                try:
                    response = urllib.request.urlopen(request, timeout=180)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.end_headers()
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (ValueError, KeyError, OSError):
                self.close_connection = True

    server = UnixServer(str(path), Handler)
    os.chmod(path, 0o600)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        path.unlink(missing_ok=True)


def bridge(socket_path):
    """Run inside the sandbox. No provider credential exists here."""
    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(socket_path)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            conn = Connection("localhost", timeout=240)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8_000_000:
                    self.send_error(413)
                    return
                conn.request("POST", self.path, self.rfile.read(size), dict(self.headers))
                response = conn.getresponse()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.end_headers()
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                conn.close()

    http.server.ThreadingHTTPServer(("127.0.0.1", 8181), Handler).serve_forever()


if __name__ == "__main__":
    import sys
    bridge(sys.argv[1])
