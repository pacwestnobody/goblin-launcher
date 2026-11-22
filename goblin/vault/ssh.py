"""Run one command on a lab host over SSH with a vault key.

Host keys are pinned in <data>/ssh/known_hosts (StrictHostKeyChecking=yes): a host that isn't
pinned is refused, not trusted on first use. Pin a host as root: `goblin-vault pin-host ID`.
The private key exists on disk only for the length of the call, in a mode-600 temp file.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .crypto import VaultError, secret_of


def known_hosts(data: Path) -> Path:
    d = Path(data) / "ssh"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / "known_hosts"
    if not p.is_file():
        p.write_text("# goblin-vault pinned host keys\n")
        p.chmod(0o600)
    return p


def _lookup(host: str, port: int) -> str:
    return host if int(port) == 22 else f"[{host}]:{port}"


def is_pinned(data: Path, host: str, port: int = 22) -> bool:
    r = subprocess.run(["ssh-keygen", "-F", _lookup(host, port), "-f", str(known_hosts(data))],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0 and bool(r.stdout.strip())


def pin_host(data: Path, host: str, port: int = 22) -> str:
    """Fetch and pin a host's keys (root CLI only; you are vouching for what the network says)."""
    r = subprocess.run(["ssh-keyscan", "-p", str(port), "-T", "8", host], capture_output=True, text=True, timeout=20)
    keys = "\n".join(l for l in r.stdout.splitlines() if l and not l.startswith("#"))
    if not keys:
        raise VaultError(f"no SSH host keys from {host}:{port}")
    with known_hosts(data).open("a") as f:
        f.write(keys + "\n")
    return keys


def run(data: Path, target: dict, cred: dict, command: str, timeout: int = 120) -> dict:
    if cred["kind"] != "ssh_private_key":
        raise VaultError(f"credential {cred['id']} is not an SSH key")
    host, port = target["host"], int(target.get("port") or 22)
    user = cred["fields"].get("username") or target.get("user") or "root"
    if "@" in user:
        user = user.split("@", 1)[0]
    if not is_pinned(data, host, port):
        raise VaultError(f"{host} is not pinned; as root run: goblin-vault pin-host {target['id']}")
    key = secret_of(cred)
    fd, key_path = tempfile.mkstemp(prefix="goblin-key-")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (key if key.endswith("\n") else key + "\n").encode())
        os.close(fd)
        argv = ["ssh", "-i", key_path, "-p", str(port), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={known_hosts(data)}", "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10", f"{user}@{host}", command]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise VaultError(f"timed out after {timeout}s") from e
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    finally:
        Path(key_path).unlink(missing_ok=True)


def allowed(target: dict, command: str, full_access: bool) -> bool:
    """The host's program list (first word), unless its credential has full access."""
    if full_access:
        return True
    progs = target.get("allowed_commands") or []
    cmd = command.strip()
    return any(cmd == p or cmd.startswith(p + " ") for p in progs)
