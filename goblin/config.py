"""Goblin Launcher's configuration: built-in defaults + ~/.config/goblin/config.yaml.

Everything specific to one person's setup (their name, machines, models, bots, lab hosts) lives
in that file, not in the code. config.example.yaml documents every key.
Secrets never go in it: providers name an environment variable or a mode-600 key file.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("GOBLIN_CONFIG_DIR", Path.home() / ".config" / "goblin"))
CONFIG_FILE = Path(os.environ.get("GOBLIN_CONFIG", CONFIG_DIR / "config.yaml"))   # GOBLIN_CONFIG: e.g. the trial

DEFAULTS: dict = {
    "owner": "the owner",                  # how agents refer to you
    "machine": "this PC",                  # how agents refer to this computer
    "paths": {
        "state": "~/.local/share/goblin",  # rooms, traces, card store, logs
        "knowledge": "~/knowledge",        # optional notes library the librarian reads
    },
    "ui": {"host": "127.0.0.1", "port": 8770},
    "room": {"host": "127.0.0.1", "port": 8765, "max_rounds": 20, "tool_result_cap": 4000,
             "shell_timeout": 180},
    "gpu": {"provider": "local", "exclusive": True},   # one local model on that Ollama at a time
    "providers": {
        "local": {"type": "ollama", "base_url": "http://127.0.0.1:11434"},
    },
    "cli_agents": {
        "grok": {"bin": "~/.grok/bin/grok", "default_model": "grok-4.7"},
        "claude": {"bin": "~/.local/bin/claude"},
    },
    "seats": {},
    "bridge": {"room_id": "phone-bridge", "authors": {}, "wake": {"enabled": False}},
    "vault": {"socket": "/run/goblin-vault/vault.sock", "token_file": "~/.config/goblin/vault-token"},
}


REPLACE = {"providers", "seats", "dials", "lab_hosts"}     # your own maps replace the defaults, never mix


def _merge(base: dict, over: dict, top: bool = True) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if top and k in REPLACE:
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v, top=False)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(dict):
    def path(self, *keys: str) -> Path:
        """A path value from the config, ~ expanded. path("paths", "state")."""
        v = self
        for k in keys:
            v = v[k]
        return Path(str(v)).expanduser()

    @property
    def state(self) -> Path:
        p = self.path("paths", "state")
        p.mkdir(parents=True, exist_ok=True)
        return p


def load(path: Path | None = None, overrides: dict | None = None) -> Config:
    path = Path(path) if path else CONFIG_FILE
    user = {}
    if path.is_file():
        user = yaml.safe_load(path.read_text()) or {}
    return Config(_merge(_merge(DEFAULTS, user), overrides or {}))


def runtime_dir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/goblin-{os.getuid()}")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def socket_path() -> Path:
    return runtime_dir() / "goblin.sock"
