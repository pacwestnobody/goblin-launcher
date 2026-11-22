"""HTTP over a unix socket in front of VaultService (stdlib only).

Headers: Authorization: Bearer <token>   X-Vault-Pin: <PIN> (changes)
         X-Goblin-Agent: <name>          X-Goblin-No-Prompt: 1 (background checks)
"""
from __future__ import annotations

import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .crypto import VaultError
from .service import Denied, VaultService, dump

MAX_BODY = 64 * 1024


def routes(v: VaultService):
    """(method, path) -> handler(who, body, query, pin). `who` is the verified caller."""
    def changes(fn):              # a change: the vault PIN in X-Vault-Pin first
        def wrapped(who, body, q, pin):
            v.check_pin(pin)
            return fn(who, body, q, pin)
        return wrapped

    def main_only(fn):            # the launcher's token, not a phone's
        def wrapped(who, body, q, pin):
            if who.get("kind") != "main":
                raise Denied(403, "not from a phone")
            return fn(who, body, q, pin)
        return wrapped

    def extend(who, b, q, pin):   # the launcher (vault PIN) or a phone (its own PIN)
        if who.get("link_id") and not pin:
            v.check_phone_pin(who, str(b.get("phone_pin") or ""))
        else:
            v.check_pin(pin)
        return v.extend(int(b.get("minutes") or 15), str(b.get("mode") or "add"))

    return {
        ("GET", "/status"): lambda w, b, q, p: v.status(),
        ("POST", "/init"): main_only(lambda w, b, q, p: v.init(str(b.get("pin") or ""))),
        ("POST", "/unlock"): lambda w, b, q, p: v.unlock(str(b.get("pin") or "")),   # the vault PIN: any caller
        ("POST", "/lock"): lambda w, b, q, p: v.lock("phone" if w.get("link_id") else ""),
        ("POST", "/extend"): extend,
        ("POST", "/phone/unlock"): lambda w, b, q, p: v.phone_unlock(w, str(b.get("phone_pin") or "")),
        ("POST", "/phone/check"): lambda w, b, q, p: v.bind_check(w),
        ("GET", "/approvals"): lambda w, b, q, p: v.broker.listing(),
        ("GET", "/phones"): main_only(lambda w, b, q, p: {"phones": v.phones()}),
        ("GET", "/devices"): main_only(lambda w, b, q, p: {"devices": v.status()["devices"]}),
        ("GET", "/devices/removable"): main_only(lambda w, b, q, p: {"drives": v.usb.drives()}),
        ("GET", "/rules"): main_only(lambda w, b, q, p: (v.need_open(), v.rules())[1]),
        ("GET", "/credentials"): main_only(lambda w, b, q, p: {"credentials": v.credentials()}),
        ("GET", "/log"): main_only(lambda w, b, q, p: {"events": v.log(int((q.get("limit") or ["60"])[0]))}),
        ("POST", "/pin-check"): main_only(changes(lambda w, b, q, p: {"ok": True})),
        ("POST", "/pin"): main_only(changes(lambda w, b, q, p: v.change_pin(str(b.get("new_pin") or "")))),
        ("POST", "/rules"): main_only(changes(lambda w, b, q, p: v.save_rules(b.get("targets")))),
        ("POST", "/phones/pair"): main_only(changes(lambda w, b, q, p: v.pair_phone(
            str(b.get("device_id") or ""), str(b.get("phone_pin") or ""), str(b.get("label") or "phone")))),
        ("POST", "/devices/enroll-usb"): main_only(changes(lambda w, b, q, p: v.enroll_usb(
            str(b.get("disk_path") or ""), str(b.get("confirm") or ""), str(b.get("label") or "")))),
        ("POST", "/backup"): main_only(changes(lambda w, b, q, p: v.backup())),
    }


def prefixed(v: VaultService, method: str, path: str):
    """Routes with an id in the path."""
    parts = path.strip("/").split("/")
    if method == "POST" and len(parts) == 3 and parts[0] == "phones" and parts[2] in ("unpair", "pin"):
        lid = parts[1]
        if parts[2] == "unpair":
            return lambda w, b, q, p: (_main(w), v.check_pin(p), v.unpair(lid))[2]
        return lambda w, b, q, p: (_main(w), v.check_pin(p), v.set_phone_pin(lid, str(b.get("phone_pin") or "")))[2]
    if method == "POST" and len(parts) == 3 and parts[0] == "devices" and parts[2] == "revoke":
        return lambda w, b, q, p: (_main(w), v.check_pin(p), v.revoke_device(parts[1]))[2]
    if method == "POST" and len(parts) == 2 and parts[0] == "approvals":
        return lambda w, b, q, p: v.decide(w, parts[1], bool(b.get("approve")), pin=p,
                                           phone_pin=str(b.get("phone_pin") or ""),
                                           remember_minutes=int(b.get("remember_minutes") or 0))
    return None


def _main(who):
    if who.get("kind") != "main":
        raise Denied(403, "not from a phone")


def make_handler(v: VaultService):
    table = routes(v)
    big_lock = threading.RLock()       # one change at a time; /run waits for approval outside it

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def address_string(self):
            return "unix"

        def _send(self, code: int, obj) -> None:
            data = dump(obj)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str):
            url = urlparse(self.path)
            path, q = url.path, parse_qs(url.query)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise Denied(413, "request too large")
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    body = {}
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            except Denied as e:
                return self._send(e.status, {"error": str(e)})
            if path == "/health":
                return self._send(200, {"ok": True, "service": "goblin-vault"})
            auth = self.headers.get("Authorization") or ""
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            pin = (self.headers.get("X-Vault-Pin") or "").strip()
            try:
                who = v.caller(token)
                if method == "POST" and path == "/run":
                    if who.get("kind") != "main":
                        raise Denied(403, "not from a phone")
                    out = v.run(str(body.get("target_id") or ""), str(body.get("command") or ""),
                                self.headers.get("X-Goblin-Agent") or "unknown", int(body.get("timeout") or 120),
                                no_prompt=self.headers.get("X-Goblin-No-Prompt") == "1")
                    return self._send(200, out)
                fn = table.get((method, path)) or prefixed(v, method, path)
                if fn is None:
                    return self._send(404, {"error": f"no route {method} {path}"})
                with big_lock:
                    return self._send(200, fn(who, body, q, pin))
            except Denied as e:
                return self._send(e.status, {"error": str(e)})
            except VaultError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - one bad request must not kill the vault
                v.audit.write("internal_error", route=path, error=f"{type(e).__name__}: {e}")
                return self._send(500, {"error": "internal error (see the vault log)"})

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    return H


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(home: Path, sock: Path) -> None:
    v = VaultService(home)
    sock = Path(sock)
    sock.unlink(missing_ok=True)
    srv = UnixServer(str(sock), make_handler(v))
    os.chmod(sock, 0o666)        # local callers still need a token; the socket itself is open
    v.audit.write("vault_started")
    srv.serve_forever()
