"""Loopback read-only report serving with an explicit root and random capability."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import quote, unquote, urlsplit


def safe_path(root, relative):
    root = Path(root).resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or "\\" in relative:
        raise ValueError("Invalid artifact path")
    path = (root / rel).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Artifact is outside the selected report directory")
    return path


class ReportServer:
    def __init__(self, root, related_roots=()):
        self.root = Path(root).resolve(strict=True)
        self.roots = (self.root, *(Path(p).resolve(strict=True) for p in related_roots))
        if any(not p.is_dir() for p in self.roots):
            raise ValueError("Report roots must be directories")
        self.token = secrets.token_urlsafe(24)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                expected_hosts = {f"127.0.0.1:{owner.server.server_port}", f"localhost:{owner.server.server_port}"}
                if self.headers.get("Host") not in expected_hosts:
                    self.send_error(403)
                    return
                prefix = "/" + owner.token + "/fs/"
                path = unquote(urlsplit(self.path).path)
                if not path.startswith(prefix):
                    self.send_error(404)
                    return
                try:
                    relative = path[len(prefix):]
                    if ".." in Path(relative).parts or "\\" in relative:
                        raise ValueError("Invalid path")
                    file = Path("/" + relative).resolve(strict=True)
                    if not file.is_file() or not any(file.is_relative_to(root) for root in owner.roots):
                        raise ValueError("File outside explicitly selected roots")
                except (ValueError, OSError):
                    self.send_error(404)
                    return
                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(file.stat().st_size))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    with file.open("rb") as stream:
                        while block := stream.read(1024 * 1024):
                            self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, relative):
        safe_path(self.root, relative)
        path = safe_path(self.root, relative)
        return f"http://127.0.0.1:{self.server.server_port}/{self.token}/fs{quote(str(path), safe='/')}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
