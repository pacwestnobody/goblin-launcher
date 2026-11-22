"""Talk to goblin-vault over its unix socket (used by the launcher, hooks, MCP, phone listener)."""
from __future__ import annotations

import http.client
import json
import os
import socket
from pathlib import Path

SOCKET = os.environ.get("GOBLIN_VAULT_SOCKET", "/run/goblin-vault/vault.sock")


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def call(method: str, path: str, body: dict | None = None, *, token: str, pin: str = "", agent: str = "",
         no_prompt: bool = False, timeout: float = 30, sock: str | None = None) -> tuple[int, dict]:
    """(status, json). status 0 = the vault isn't reachable."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if pin:
        headers["X-Vault-Pin"] = pin
    if agent:
        headers["X-Goblin-Agent"] = agent
    if no_prompt:
        headers["X-Goblin-No-Prompt"] = "1"
    conn = _UnixHTTP(sock or SOCKET, timeout)
    try:
        conn.request(method, path, json.dumps(body or {}) if method != "GET" else None, headers)
        r = conn.getresponse()
        raw = r.read()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {"error": raw.decode(errors="replace")[:300]}
        return r.status, data
    except (OSError, http.client.HTTPException) as e:
        return 0, {"error": f"vault unreachable: {e}"}
    finally:
        conn.close()


def main_token(path: str | Path | None = None) -> str:
    """The launcher's token: a copy the installer places in the user's config (mode 600)."""
    p = Path(path or os.environ.get("GOBLIN_VAULT_TOKEN_FILE",
                                    Path.home() / ".config" / "goblin" / "vault-token"))
    try:
        return p.read_text().strip()
    except OSError:
        return ""
