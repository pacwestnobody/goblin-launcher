"""lab_run: one shell command on a lab host, through goblin-vault (agents never see a key).

The vault decides everything: whether that host takes writes, its program list, and the approval
(the call waits while the owner decides). Host descriptions for the tool text come from config
`lab_hosts: {id: "what it is"}`; the list of hosts itself comes from the vault.
"""
from __future__ import annotations

from .vault import client

PARAMS = {"type": "object", "properties": {
    "target": {"type": "string", "description": "a lab host id from the list"},
    "command": {"type": "string", "description": "the shell command to run there"}},
    "required": ["target", "command"]}


def _vault(cfg) -> dict:
    v = cfg.get("vault") or {}
    return {"sock": v.get("socket"), "token": client.main_token(cfg.path("vault", "token_file"))}


def hosts(cfg) -> list[str]:
    code, st = client.call("GET", "/status", **_vault(cfg), timeout=5)
    return st.get("targets") or [] if code == 200 else sorted((cfg.get("lab_hosts") or {}))


def describe(cfg) -> str:
    notes = cfg.get("lab_hosts") or {}
    listing = "; ".join(f"{h} = {notes.get(h, 'lab host')}" for h in hosts(cfg)) or "none configured"
    return (f"Run a shell command on one of {cfg.get('owner', 'the owner')}'s lab hosts through the vault. "
            f"Each host follows its own rule (read-only or full access); the owner approves, and the call "
            f"waits. Say what a change does and how to undo it before running it. Hosts: {listing}")


def spec(cfg) -> dict:
    return {"type": "function", "function": {"name": "lab_run", "description": describe(cfg), "parameters": PARAMS}}


def run(cfg, target: str, command: str, agent: str = "agent", timeout: int = 120) -> tuple[int | None, str]:
    """(exit code or None, text). The HTTP wait covers the approval timeout plus the command."""
    code, data = client.call("POST", "/run", {"target_id": target, "command": command, "timeout": timeout},
                             agent=agent, timeout=timeout + 300, **_vault(cfg))
    if code == 0:
        return None, f"vault unavailable: {data.get('error')}"
    if code == 423:
        return None, "the vault is locked; the owner can unlock it in Goblin Launcher or on the phone"
    if code >= 400:
        return 1, f"refused: {data.get('error')}"
    out = (data.get("stdout") or "") + (("\n" + data["stderr"]) if data.get("stderr") else "")
    return data.get("exit_code"), out
