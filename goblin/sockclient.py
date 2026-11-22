"""Call the running Goblin Launcher over its unix socket. Import-light (hooks run on every prompt).
Fails soft: None when the service is down or answers with an error."""
from __future__ import annotations

import http.client
import json
import socket

from .config import socket_path


class _Unix(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self._path))


def call(method: str, path: str, body: dict | None = None, timeout: float = 20, sock=None):
    try:
        c = _Unix(sock or socket_path(), timeout)
        c.request(method, path, None if body is None else json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return None if r.status >= 400 else json.loads(raw or b"{}")
    except (OSError, ValueError, http.client.HTTPException):
        return None
