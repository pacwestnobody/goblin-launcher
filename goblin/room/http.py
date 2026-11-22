"""The room's HTTP API (127.0.0.1:8765 by default). Paths unchanged, so bots keep working:

  GET  /api/status                       seats + bridge info
  GET  /api/rooms        POST /api/rooms {brief, seats, mode}
  GET  /api/rooms/ID     POST /api/rooms/ID/message {text}
  GET  /api/bridge[?after_id=]           POST /api/bridge/message {author_id, text, client_nonce?}
  GET  /api/bridge/wait?after_id=&timeout_s=
Loopback only. Browsers from other sites are refused (no CORS), so a web page can't post as you.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import Room


def make_handler(room: Room):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _cross_site(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return False
            host = urlparse(origin).hostname or ""
            return host not in ("127.0.0.1", "localhost")

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            p = u.path.rstrip("/") or "/"
            if p == "/api/status":
                return self._send(200, room.status())
            if p == "/api/rooms":
                return self._send(200, {"rooms": room.rooms.listing()})
            if p.startswith("/api/rooms/"):
                r = room.rooms.get(p.split("/")[3]) if len(p.split("/")) == 4 else None
                return self._send(200, r) if r else self._send(404, {"error": "no such room"})
            if p == "/api/bridge":
                return self._send(200, room.bridge_after((q.get("after_id") or [""])[0]))
            if p == "/api/bridge/wait":
                after = (q.get("after_id") or [""])[0]
                if not after:
                    return self._send(400, {"error": "after_id required"})
                try:
                    t = float((q.get("timeout_s") or ["25"])[0])
                except ValueError:
                    return self._send(400, {"error": "bad timeout_s"})
                return self._send(200, room.bridge_wait(after, t))
            if p == "/":
                ui = room.cfg.get("ui") or {}
                self.send_response(302)
                self.send_header("Location", f"http://{ui.get('host', '127.0.0.1')}:{ui.get('port', 8770)}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self._cross_site():
                return self._send(403, {"error": "cross-site request refused"})
            p = urlparse(self.path).path.rstrip("/")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 256 * 1024)) or b"{}") if n else {}
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            try:
                if p == "/api/rooms":
                    return self._send(200, room.create(str(body.get("brief") or ""), list(body.get("seats") or []),
                                                       str(body.get("mode") or "sequential")))
                if p == "/api/bridge/message":
                    return self._send(200, room.bridge_post(str(body.get("author_id") or ""), str(body.get("text") or ""),
                                                            str(body.get("client_nonce") or "")))
                parts = p.split("/")
                if len(parts) == 5 and parts[1:3] == ["api", "rooms"] and parts[4] == "message":
                    return self._send(200, room.say(parts[3], str(body.get("text") or "")))
            except KeyError:
                return self._send(404, {"error": "no such room"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})
    return H


def serve(room: Room) -> ThreadingHTTPServer:
    rc = room.cfg.get("room") or {}
    srv = ThreadingHTTPServer((rc.get("host", "127.0.0.1"), int(rc.get("port", 8765))), make_handler(room))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
