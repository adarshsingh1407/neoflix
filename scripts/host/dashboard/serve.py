#!/usr/bin/env python3
"""Static file server for the host dashboard.

Why not plain `python3 -m http.server`: its listen backlog is 5, so a page
that requests ~30 images at once (poster wall, top shelf, icons) gets
connection resets and broken images. This keeps the same stdlib server with
a larger backlog, quiet logs, and long cache lifetimes for artwork/icons
(their filenames change when the image changes, so caching is safe).

Usage: serve.py <port> <directory>   (stdlib only, macOS /usr/bin/python3)
"""
import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        if self.path.startswith(("/media/", "/icons/")) and not self.path.endswith(".json"):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass  # one line per request isn't worth keeping


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: serve.py <port> <directory>")
    port, directory = int(sys.argv[1]), sys.argv[2]
    handler = functools.partial(Handler, directory=directory)
    with Server(("0.0.0.0", port), handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
