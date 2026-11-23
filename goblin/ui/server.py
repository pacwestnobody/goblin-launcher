"""The launcher page's server (127.0.0.1 only): static files + a JSON API, routes grouped by area.

Areas: dials & GPU · agents · room (proxied, same origin) · vault & workshop · stack levers ·
shelf · ledger (Path Librarian jobs, cards, report, sandbox). Cross-site POSTs are refused, so
a web page open in the browser can't drive any of this.
"""
from __future__ import annotations

import io
import json
import mimetypes
import re
import threading
import contextlib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import gpu, stack
from ..agents import cli as agents
from ..vault import client as vault
from .jobs import Jobs

STATIC = Path(__file__).resolve().parent / "static"


class Api:
    def __init__(self, service):
        self.s = service
        self.cfg = service.cfg
        self.jobs = Jobs(service.librarian_cfg)
        self.loading: set[str] = set()
        self.routes: list[tuple[str, re.Pattern, callable]] = []
        for method, path, fn in self.table():
            pat = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
            self.routes.append((method, pat, fn))

    def table(self):
        return [
            # dials & GPU
            ("GET", "/api/dials", lambda q, b: {"furnace": [], "aether": [], **(self.cfg.get("dials") or {}),
                                                "brand": " · ".join(x for x in (self.cfg.get("machine"), self.cfg.get("tagline")) if x)}),
            ("GET", "/api/gpu", lambda q, b: self.gpu_reading()),
            ("POST", "/api/furnace/load", lambda q, b: self.furnace_load(str(b.get("model") or ""))),
            # agents
            ("GET", "/api/agents", lambda q, b: agents.list_agents(self.cfg)),
            ("POST", "/api/agents/launch", lambda q, b: agents.launch(self.cfg, str(b.get("id") or ""), b.get("cwd") or None)),
            # stack
            ("GET", "/api/stack/status", lambda q, b: stack.status(self.cfg)),
            ("POST", "/api/stack/off", lambda q, b: {"log": stack.power_off(self.cfg)}),
            ("POST", "/api/stack/on", lambda q, b: {"log": stack.power_on(self.cfg)}),
            ("POST", "/api/stack/ollama", lambda q, b: {"log": [stack.start_ollama(self.cfg)]}),
            ("POST", "/api/stack/unload", lambda q, b: {"log": [stack.unload_all(self.cfg)]}),
            # shelf
            ("GET", "/api/shelf", lambda q, b: self.s.librarian.shelf()),
            ("POST", "/api/shelf/check", lambda q, b: self.s.librarian.check_shelf()),
            # vault: reads
            ("GET", "/api/vault/status", lambda q, b: self.vault_status()),
            ("GET", "/api/vault/approvals", lambda q, b: self.v("GET", "/approvals")),
            ("GET", "/api/vault/devices", lambda q, b: self.v("GET", "/devices")),
            ("GET", "/api/vault/removable", lambda q, b: self.v("GET", "/devices/removable")),
            ("GET", "/api/vault/phones", lambda q, b: self.v("GET", "/phones")),
            ("GET", "/api/vault/rules", lambda q, b: self.v("GET", "/rules")),
            ("GET", "/api/vault/log", lambda q, b: self.v("GET", "/log?limit=60")),
            # vault: changes (the PIN travels with each one)
            ("POST", "/api/vault/unlock", lambda q, b: self.v("POST", "/unlock", {"pin": str(b.get("pin") or "")})),
            ("POST", "/api/vault/lock", lambda q, b: self.v("POST", "/lock")),
            ("POST", "/api/vault/extend", lambda q, b: self.v("POST", "/extend", {"minutes": int(b.get("minutes") or 15),
                                                                                  "mode": "add"}, b)),
            ("POST", "/api/vault/approvals/{id}", lambda q, b, id: self.v("POST", f"/approvals/{id}", {
                "approve": bool(b.get("approve")), "remember_minutes": int(b.get("remember_minutes") or 0)}, b)),
            ("POST", "/api/vault/revoke/{id}", lambda q, b, id: self.v("POST", f"/devices/{id}/revoke", {}, b)),
            ("POST", "/api/vault/unpair/{id}", lambda q, b, id: self.v("POST", f"/phones/{id}/unpair", {}, b)),
            ("POST", "/api/vault/enroll-usb", lambda q, b: self.v("POST", "/devices/enroll-usb", {
                "disk_path": b.get("disk_path", ""), "confirm": b.get("confirm", ""), "label": b.get("label", "")}, b, 400)),
            ("POST", "/api/vault/rules", lambda q, b: self.v("POST", "/rules", {"targets": b.get("targets")}, b)),
            ("POST", "/api/vault/change-pin", lambda q, b: self.v("POST", "/pin", {"new_pin": b.get("new_pin", "")}, b)),
            ("POST", "/api/vault/phone-pin", lambda q, b: self.v("POST", f"/phones/{b.get('link_id', '')}/pin",
                                                                 {"phone_pin": b.get("phone_pin", "")}, b)),
            ("POST", "/api/vault/backup", lambda q, b: self.v("POST", "/backup", {}, b, 120)),
            # phones (setup over USB)
            ("GET", "/api/phone/status", lambda q, b: self.phone().adb_status(self.cfg)),
            ("POST", "/api/phone/pair", lambda q, b: self.phone().pair(self.cfg, b)),
            ("POST", "/api/phone/push", lambda q, b: self.phone().push(self.cfg)),
            ("POST", "/api/phone/rotate-cert", lambda q, b: self.phone().rotate(self.cfg, b)),
            # ledger
            ("GET", "/api/ledger/status", lambda q, b: self.ledger_status()),
            ("GET", "/api/ledger/cards", lambda q, b: self.cards()),
            ("GET", "/api/ledger/report", lambda q, b: {"text": self.report()}),
            ("GET", "/api/ledger/trace/{id}", lambda q, b, id: self.trace(id)),
            ("POST", "/api/ledger/jobs", lambda q, b: self.jobs.start(b).summary()),
            ("GET", "/api/ledger/jobs/{id}/events", lambda q, b, id: self.job_events(id, int((q.get("after") or ["0"])[0]))),
            ("POST", "/api/ledger/jobs/{id}/confirm", lambda q, b, id: {"ok": self.jobs.answer(id, b.get("ok"))}),
            ("POST", "/api/ledger/jobs/{id}/stop", lambda q, b, id: {"ok": self.jobs.stop(id)}),
            ("POST", "/api/ledger/sandbox/{action}", lambda q, b, action: self.sandbox(action)),
            ("GET", "/api/ledger/pending", lambda q, b: self.pending()),
            ("POST", "/api/ledger/pending/write", lambda q, b: self.write_pending()),
        ]

    # ---- helpers ------------------------------------------------------------------
    def v(self, method, path, body=None, b=None, timeout=30):
        code, data = vault.call(method, path, body, token=vault.main_token(self.cfg.path("vault", "token_file")),
                                pin=str((b or {}).get("pin") or ""), sock=(self.cfg.get("vault") or {}).get("socket"),
                                timeout=timeout)
        if code != 200:
            raise ApiError(code or 502, data.get("error") or f"vault error {code}")
        return data

    def vault_status(self):
        try:
            return {"reachable": True, **self.v("GET", "/status")}
        except ApiError as e:
            return {"reachable": False, "error": str(e)}

    @staticmethod
    def phone():
        from .. import phone
        return phone

    def gpu_reading(self):
        best = None
        for dev in Path("/sys/class/drm").glob("card[0-9]*/device"):
            try:
                total, used = int((dev / "mem_info_vram_total").read_text()), int((dev / "mem_info_vram_used").read_text())
            except (OSError, ValueError):
                continue
            if not best or total > best[0]:
                best = (total, used)
        loaded, up = [], False
        p = gpu.provider(self.cfg)
        if p:
            try:
                with urllib.request.urlopen(p.base_url.rstrip("/").removesuffix("/v1") + "/api/ps", timeout=2) as r:
                    loaded = [{"name": m["name"], "vram": m.get("size_vram", 0)} for m in json.loads(r.read()).get("models", [])]
                    up = True
            except (OSError, ValueError):
                pass
        gb = 1024 ** 3
        return {"total_gb": round(best[0] / gb, 1) if best else None, "used_gb": round(best[1] / gb, 1) if best else None,
                "ollama_up": up, "loaded": loaded, "loading": sorted(self.loading)}

    def furnace_load(self, model):
        """The owner turned the Furnace dial: that is the command to load this one model."""
        dials = (self.cfg.get("dials") or {}).get("furnace") or []
        allowed = {d.get("model") for d in dials} | {(d.get("mode") or {}).get("model") for d in dials}
        if not model or model not in allowed:
            raise ApiError(400, f"{model} is not on the Furnace dial")

        def work():
            self.loading.add(model)
            try:
                gpu.load(self.cfg, model)
            except Exception:  # noqa: BLE001 - shows as "not loaded" on the dial
                pass
            finally:
                self.loading.discard(model)
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "loading": model}

    # ---- ledger -------------------------------------------------------------------
    def _store(self):
        from ..librarian.store import Store
        cfg = self.s.librarian_cfg()
        return cfg, Store(cfg.path(cfg["store"]["path"]))

    def pending(self):
        lib = self.s.librarian
        return {"pending": lib.pending(), "writing": lib._writing.locked(), "last": lib.last_write,
                "writer_loaded": lib.writer_loaded(), "mode": lib.cfg["mode"]}

    def write_pending(self):
        """The owner's say-so to load the writer model and write every queued card."""
        import threading
        lib = self.s.librarian
        if lib._writing.locked():
            return {"started": False, "reason": "already writing"}
        if not lib.pending():
            return {"started": False, "reason": "nothing queued"}
        threading.Thread(target=lib.write_pending, daemon=True).start()
        return {"started": True}

    def ledger_status(self):
        cfg, st = self._store()
        try:
            recent = [dict(r) for r in st.db.execute(
                "SELECT job_id, kind, key, mode, holdout, injected_card, tokens_in, tokens_out, steps, status, "
                "verify_ok, ts FROM jobs ORDER BY ts DESC LIMIT 40")]
            writes = {r["job_id"]: {"outcome": r["outcome"], "reason": r["reason"]} for r in st.db.execute(
                "SELECT job_id, outcome, reason FROM writes ORDER BY ts DESC LIMIT 200")}
            cards, size = len(st.all_readable()), st.readable_bytes()
        finally:
            st.db.close()
        for r in recent:
            r["write"] = writes.get(r["job_id"])
        return {"mode": cfg["mode"], "roles": cfg["roles"], "models": sorted(cfg["models"]),
                "targets": {k: v.get("type") for k, v in cfg["targets"].items()},
                "default_target": "sandbox" if "sandbox" in cfg["targets"] else "local",
                "job_kinds": cfg["job_kinds"], "cards": cards, "store_bytes": size, "cap_bytes": cfg["store"]["cap_bytes"],
                "recent": recent, "live_jobs": [j.summary() for j in sorted(self.jobs.jobs.values(), key=lambda j: -j.started)]}

    def cards(self):
        _, st = self._store()
        try:
            return [{k: c[k] for k in ("id", "key", "state", "bytes", "hits", "created", "card")} for c in st.all_readable()]
        finally:
            st.db.close()

    def report(self):
        from ..librarian import cli as lcli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lcli.report(self.s.librarian_cfg())
        return buf.getvalue()

    def trace(self, job_id):
        cfg = self.s.librarian_cfg()
        p = cfg.path(cfg["trace_dir"]) / f"{Path(job_id).name}.jsonl"
        if not p.is_file():
            raise ApiError(404, "no trace")
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def job_events(self, job_id, after):
        job = self.jobs.jobs.get(job_id)
        if not job:
            raise ApiError(404, "no such job")
        return {"events": job.wait_events(after, 20), "job": job.summary()}

    def sandbox(self, action):
        from ..librarian import targets
        cfg = self.s.librarian_cfg()
        t = cfg["targets"].get("sandbox")
        if not t or t.get("type") != "pct":
            return {"ok": False, "out": "no pct sandbox target in librarian.yaml"}
        ctid = str(t["ctid"])
        argv = {"status": ["pct", "status", ctid], "reset": ["pct", "rollback", ctid, "clean"],
                "start": ["pct", "start", ctid]}.get(action)
        if not argv:
            raise ApiError(404, f"unknown action {action}")
        host = {"targets": {"h": {**{k: v for k, v in t.items() if k != "ctid"}, "type": "ssh"}}}
        code, out = targets.run(host, "h", argv, timeout=120)
        if action == "reset" and code == 0:
            code, more = targets.run(host, "h", ["pct", "start", ctid], timeout=120)
            out += "\n" + more
        return {"ok": code == 0, "code": code, "out": (out or "").strip()}

    # ---- dispatch -----------------------------------------------------------------
    def dispatch(self, method, path, query, body):
        for m, pat, fn in self.routes:
            if m == method:
                hit = pat.match(path)
                if hit:
                    return fn(query, body, **hit.groupdict())
        raise ApiError(404, "not found")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def make_handler(api: Api):
    room_cfg = api.cfg.get("room") or {}
    room_base = f"http://{room_cfg.get('host', '127.0.0.1')}:{room_cfg.get('port', 8765)}"

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://{self.headers.get('Host', '')}"

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            v = json.loads(self.rfile.read(min(n, 1024 * 1024)) or b"{}")
            return v if isinstance(v, dict) else {}

        def _room(self, method, path, body=None):
            req = urllib.request.Request(room_base + path, method=method, headers={"Content-Type": "application/json"},
                                         data=None if body is None else json.dumps(body).encode())
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return self._send(r.status, r.read())
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read())
            except OSError as e:
                return self._send(502, {"error": f"the room isn't answering: {e}"})

        def _handle(self, method):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if method == "GET" and path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                return self._send(404, {"error": "not found"})
            if method == "POST" and not self._same_origin():
                return self._send(403, {"error": "cross-site request refused"})
            try:
                body = self._body() if method == "POST" else {}
                if path.startswith("/room/api/"):
                    return self._room(method, path[len("/room"):] + (f"?{u.query}" if u.query else ""),
                                      body if method == "POST" else None)
                return self._send(200, api.dispatch(method, path, q, body))
            except ApiError as e:
                return self._send(e.status, {"error": str(e)})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")
    return H


def serve(service) -> ThreadingHTTPServer:
    ui = service.cfg.get("ui") or {}
    srv = ThreadingHTTPServer((ui.get("host", "127.0.0.1"), int(ui.get("port", 8770))), make_handler(Api(service)))
    srv.daemon_threads = True
    return srv
