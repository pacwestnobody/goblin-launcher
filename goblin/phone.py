"""The phone: its HTTPS listener, and setting a phone up over USB (adb).

Listener (config `phone:`; started by goblin serve when enabled):
  bind: 192.168.1.10   port: 8771   allow: [192.168.1.0/24, 10.8.0.0/24]
  - TLS with a self-signed certificate made here; the app pins its SHA-256, so no CA is involved.
  - Only LAN / WireGuard source addresses; everything else gets 403.
  - Only the phone's routes, forwarded to goblin-vault with the PHONE's own token: the vault lets
    that token unlock, extend, lock and decide approvals, nothing more.

Setup over USB: the Workshop's "Add a phone" (or `goblin phone setup`) finds the phone with adb,
installs the app, mints a pairing in the vault and writes it straight into the app's private
storage with `run-as`. The phone PIN is never in the pairing; the owner types it on the phone.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .vault import client as vault

PKG = "com.goblin.launcher"
GET_ROUTES = {"/health", "/status", "/approvals"}
POST_ROUTES = {"/lock", "/extend", "/unlock", "/phone/unlock", "/phone/check"}
DEFAULTS = {"enabled": False, "bind": "0.0.0.0", "port": 8771,
            "allow": ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "127.0.0.1/32"]}
_CTX: ssl.SSLContext | None = None


def settings(cfg) -> dict:
    return {**DEFAULTS, **(cfg.get("phone") or {})}


def tls_dir(cfg) -> Path:
    d = cfg.state / "phone-tls"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def ensure_cert(cfg) -> tuple[Path, Path]:
    d, bind = tls_dir(cfg), settings(cfg)["bind"]
    cert, key = d / "cert.pem", d / "key.pem"
    if not (cert.exists() and key.exists()):
        san = f"IP:{bind},IP:127.0.0.1" if re.fullmatch(r"[\d.]+", bind) and bind != "0.0.0.0" else "IP:127.0.0.1"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-days", "3650", "-subj", "/CN=goblin-launcher", "-addext", f"subjectAltName={san}",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        key.chmod(0o600)
    return cert, key


def cert_sha256(cfg) -> str:
    """SHA-256 of the certificate (DER), as the app pins it: lowercase hex, no colons."""
    out = subprocess.run(["openssl", "x509", "-in", str(tls_dir(cfg) / "cert.pem"), "-noout", "-fingerprint", "-sha256"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip().split("=", 1)[1].replace(":", "").lower()


# ---- listener ---------------------------------------------------------------------------
def make_handler(cfg):
    s = settings(cfg)
    nets = [ipaddress.ip_network(n) for n in s["allow"]]
    sock = (cfg.get("vault") or {}).get("socket")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _forward(self, method: str, path: str, body=None):
            try:
                ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return self._send(403, {"error": "unknown source"})
            if not any(ip in n for n in nets):
                return self._send(403, {"error": "phone access only from the LAN or WireGuard"})
            if path == "/health":
                return self._send(200, {"ok": True, "service": "goblin-phone"})
            auth = self.headers.get("Authorization") or ""
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not token:
                return self._send(401, {"error": "token required"})
            code, data = vault.call(method, path, body, token=token, sock=sock, timeout=150)
            return self._send(code or 502, data)

        def do_GET(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            return self._forward("GET", p) if p in GET_ROUTES else self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p not in POST_ROUTES and not re.fullmatch(r"/approvals/apr_[0-9a-f]+", p):
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}") if n else {}
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            return self._forward("POST", p, body)
    return H


def start(cfg) -> ThreadingHTTPServer:
    global _CTX
    s = settings(cfg)
    cert, key = ensure_cert(cfg)
    ctx = _CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    srv = ThreadingHTTPServer((s["bind"], int(s["port"])), make_handler(cfg))
    srv.daemon_threads = True
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"phone: https://{s['bind']}:{s['port']}/  certificate {cert_sha256(cfg)[:16]}…", flush=True)
    return srv


# ---- setup over USB -----------------------------------------------------------------------
ADB_HELP = ("adb isn't installed. On Ubuntu/Debian: sudo apt install adb (or install Android's platform-tools "
            "and set phone.adb in config.yaml).")


def adb(cfg) -> str | None:
    for p in ((cfg.get("phone") or {}).get("adb"), shutil.which("adb"), str(Path.home() / "Android/Sdk/platform-tools/adb")):
        if p and Path(str(p)).expanduser().exists():
            return str(Path(str(p)).expanduser())
    return None


def apk(cfg) -> Path | None:
    repo = Path(__file__).resolve().parent.parent
    for p in ((cfg.get("phone") or {}).get("apk"), Path(__file__).resolve().parent / "android" / "goblin-launcher.apk",
              repo / "android/app/build/outputs/apk/debug/app-debug.apk"):
        if p and Path(str(p)).expanduser().is_file():
            return Path(str(p)).expanduser()
    return None


def _run(argv: list[str], timeout: int = 60, stdin: bytes | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, input=stdin)
        return p.returncode, (p.stdout + p.stderr).decode(errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _pairing_file(cfg) -> Path:
    return cfg.state / "phone-pairing.json"


def adb_status(cfg) -> dict:
    """What the guided setup shows: adb present? phone found? authorized? app installed? pairing waiting?"""
    a = adb(cfg)
    out = {"adb": bool(a), "adb_help": None if a else ADB_HELP, "apk": bool(apk(cfg)), "phones": [],
           "pairing_waiting": _pairing_file(cfg).exists()}
    if not a:
        return out
    code, text = _run([a, "devices", "-l"], 20)
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        info = dict(x.split(":", 1) for x in parts[2:] if ":" in x)
        ph = {"serial": parts[0], "state": parts[1], "model": info.get("model", "").replace("_", " ") or parts[0]}
        if ph["state"] == "device":
            ph["app_installed"] = PKG in _run([a, "-s", ph["serial"], "shell", "pm", "list", "packages", PKG], 20)[1]
        out["phones"].append(ph)
    return out


def _one_phone(cfg) -> list[str]:
    a = adb(cfg)
    if not a:
        raise ValueError(ADB_HELP)
    ready = [p for p in adb_status(cfg)["phones"] if p["state"] == "device"]
    if not ready:
        raise ValueError("no phone ready over USB: plug it in, turn on USB debugging, and tap Allow on the phone")
    if len(ready) > 1:
        raise ValueError("more than one phone is plugged in: unplug the others")
    return [a, "-s", ready[0]["serial"]]


def pair(cfg, body: dict) -> dict:
    """Mint a link in the vault (vault PIN) and keep the pairing ready to push (mode 600)."""
    s = settings(cfg)
    code, data = vault.call("POST", "/phones/pair", {"device_id": body.get("device_id", ""), "label": body.get("label") or "phone",
                                                     "phone_pin": body.get("phone_pin", "")},
                            token=vault.main_token(cfg.path("vault", "token_file")), pin=str(body.get("pin") or ""),
                            sock=(cfg.get("vault") or {}).get("socket"))
    if code != 200:
        raise ValueError(data.get("error") or f"vault error {code}")
    ensure_cert(cfg)
    host = s.get("public_host") or s["bind"]
    bundle = {"url": f"https://{host}:{s['port']}", "cert": cert_sha256(cfg), "token": data["token"],
              "link": data["link"]["link_id"], "machine": cfg.get("machine") or "", "ssh_user": s.get("ssh_user") or "",
              "ssh_host": s.get("ssh_host") or host}
    f = _pairing_file(cfg)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(bundle))
    os.chmod(tmp, 0o600)
    tmp.replace(f)
    return {"ok": True, "link_id": bundle["link"], "next": "Now press Install on the phone."}


def push(cfg) -> dict:
    """Install (or update) the app and hand it the waiting pairing, over USB."""
    dev = _one_phone(cfg)
    package = apk(cfg)
    if not package:
        raise ValueError("the app package isn't built (android/: ./gradlew :app:assembleDebug)")
    log = []
    code, out = _run(dev + ["install", "-r", str(package)], 300)
    if code != 0:
        raise ValueError(f"install failed: {out[-300:]}")
    log.append("app installed")
    f = _pairing_file(cfg)
    if f.exists():
        # run-as writes into the app's private storage (debuggable build): no other app can write there.
        # Piped through stdin: no temp file on the phone, nothing on a command line.
        code, out = _run(dev + ["shell", f"run-as {PKG} sh -c 'mkdir -p files && cat > files/pairing.json'"], 60, f.read_bytes())
        if code != 0 or out:
            raise ValueError(f"pairing not written: {out[-300:]}")
        f.unlink()
        log += ["pairing written into the app's private storage", "pairing file deleted from this computer"]
    _run(dev + ["shell", "am", "force-stop", PKG], 30)
    _run(dev + ["shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1"], 30)
    log.append("app opened: type the phone PIN on the phone")
    return {"ok": True, "log": log}


def rotate(cfg, body: dict) -> dict:
    """New listener certificate (vault PIN first). Paired phones must be paired again."""
    code, data = vault.call("POST", "/pin-check", {}, token=vault.main_token(cfg.path("vault", "token_file")),
                            pin=str(body.get("pin") or ""), sock=(cfg.get("vault") or {}).get("socket"))
    if code != 200:
        raise ValueError(data.get("error") or f"vault error {code}")
    for f in ("cert.pem", "key.pem"):
        (tls_dir(cfg) / f).unlink(missing_ok=True)
    cert, key = ensure_cert(cfg)
    if _CTX is not None:
        _CTX.load_cert_chain(str(cert), str(key))
    return {"ok": True, "cert": cert_sha256(cfg)[:16], "next": "Pair the phone again: it still trusts the old certificate."}
