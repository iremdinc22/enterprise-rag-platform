"""Temporary static infrastructure page; replaced by Vite in the frontend task."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Enterprise RAG Demo</title><body><h1>Enterprise RAG Demo</h1>
<p>Isolated infrastructure placeholder. Dashboard is not implemented yet.</p>
<p>Provider mode: disabled. No login, upload or query UI is available.</p></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        content = b"ok" if self.path == "/health" else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if self.path == "/health" else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


def main():
    ThreadingHTTPServer(("0.0.0.0", 5173), Handler).serve_forever()


if __name__ == "__main__":
    main()
