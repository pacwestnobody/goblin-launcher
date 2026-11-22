"""`goblin serve`: the one user service (goblin.service).

  launcher page   http://127.0.0.1:8770        (config ui)
  room + bridge   http://127.0.0.1:8765        (config room)  bots and agents post here
  agent socket    $XDG_RUNTIME_DIR/goblin.sock (mode 600)    hooks + MCP: /v1/context /v1/trace /v1/end /v1/shelf
  phone           https://<LAN IP>:8771        (config phone) the paired app, pinned certificate
The vault is a separate system service (goblin-vault) that this talks to over its own socket.
A part that fails to start is reported and skipped; the rest keeps running.
"""
from __future__ import annotations

import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

from . import config as config_mod


class Service:
    def __init__(self, cfg):
        from .librarian.runtime import Librarian
        from .room.core import Room
        self.cfg = cfg
        self.librarian = Librarian(self.librarian_cfg())
        self.room = Room(cfg, self.librarian)

    def librarian_cfg(self):
        from .librarian import config as lconf
        return lconf.load(goblin=self.cfg)


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def socket_handler(service: Service):
    lib = service.librarian

    def lab_run(b):           # for harnesses that call the socket instead of MCP (OpenClaw's plugin)
        from . import lab
        code, out = lab.run(service.cfg, str(b.get("target") or ""), str(b.get("command") or ""),
                            agent=str(b.get("agent") or "agent"))
        return {"exit": code, "output": out}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def address_string(self):
            return "unix"

        def _send(self, code, obj):
            data = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p == "/v1/health":
                return self._send(200, {"ok": True, **lib.health()})
            if p == "/v1/shelf":
                return self._send(200, lib.shelf())
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                fn = {"/v1/context": lib.context_full, "/v1/trace": lib.trace_full, "/v1/end": lib.end_full,
                      "/v1/shelf/check": lambda b: lib.check_shelf(), "/v1/lab_run": lab_run}.get(p)
                if fn is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, fn(body))
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
    return H


def _start(name: str, fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - one part failing must not take the rest down
        print(f"[goblin] {name} not started: {type(e).__name__}: {e}", flush=True)


def main(cfg=None, argv=None) -> int:
    from .room import http as room_http
    from .ui import server as ui_server
    cfg = cfg or config_mod.load()
    svc = Service(cfg)
    svc.librarian.start()

    sock = config_mod.socket_path()
    sock.unlink(missing_ok=True)
    usrv = UnixServer(str(sock), socket_handler(svc))
    os.chmod(sock, 0o600)
    threading.Thread(target=usrv.serve_forever, daemon=True).start()

    _start("room", lambda: room_http.serve(svc.room))

    def phone():
        from . import phone as phone_mod
        phone_mod.start(cfg)
    if (cfg.get("phone") or {}).get("enabled"):
        _start("phone listener", phone)

    ui = ui_server.serve(svc)
    print(f"Goblin Launcher: http://{ui.server_address[0]}:{ui.server_address[1]}/  socket {sock}", flush=True)
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        svc.librarian.stop()
        usrv.shutdown()
        sock.unlink(missing_ok=True)
    return 0
