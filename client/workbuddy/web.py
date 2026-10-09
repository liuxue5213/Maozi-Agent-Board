"""Local only settings and live preview server (stdlib HTTP)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .profile import validate_profile

WEB_ROOT = Path(__file__).with_name("web_assets")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.css": ("app.css", "text/css; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8")}


def start_settings_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _write(self, status, body, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, data):
            self._write(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/status":
                self._json(200, app.status_snapshot())
                return
            if path == "/api/autostart-status":
                try:
                    from . import autostart
                    self._json(200, {"enabled": autostart.status()})
                except Exception:
                    self._json(200, {"enabled": False, "unsupported": True})
                return
            if path not in FILES:
                self._json(404, {"error": "未找到页面"})
                return
            filename, mime = FILES[path]
            self._write(200, (WEB_ROOT / filename).read_bytes(), mime)

        def do_POST(self):
            path = urlsplit(self.path).path
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._json(415, {"error": "需要 application/json"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16384:
                self._json(413, {"error": "请求过大"})
                return
            try:
                data = json.loads(self.rfile.read(length))
                if path == "/api/validate":
                    self._json(200, {"profile": validate_profile(data)})
                elif path == "/api/autostart":
                    from . import autostart
                    self._json(200, {"enabled": autostart.set_enabled(bool(data.get("enable")))})
                elif path == "/api/save":
                    self._json(200, {"profile": app.save_profile(data), "status": "已保存，待同步"})
                elif path == "/api/sync":
                    app.request_sync()
                    self._json(200, {"status": "待同步"})
                else:
                    self._json(404, {"error": "未找到接口"})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
