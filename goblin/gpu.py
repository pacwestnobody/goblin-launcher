"""One local model at a time.

Local models share one GPU. Before a local model runs, every other model on that Ollama is
unloaded, and local turns take a lock so two never load at once (two 27B models resident together
spilled out of 16 GB and hung the GPU's compute ring on 2026-11-22). Fails soft: if Ollama can't
be reached, nothing is unloaded and the caller carries on.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from .providers import ProviderError, make
from .providers.ollama import Ollama

turn_lock = threading.Lock()


def _norm(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def provider(cfg) -> Ollama | None:
    name = (cfg.get("gpu") or {}).get("provider")
    pc = (cfg.get("providers") or {}).get(name)
    if not pc or pc.get("type") != "ollama":
        return None
    return make(name, pc)


def ensure_up(cfg, wait_s: int = 30) -> bool:
    p = provider(cfg)
    if p is None:
        return False
    try:
        p.loaded()
        return True
    except OSError:
        pass
    start = (cfg["providers"][cfg["gpu"]["provider"]]).get("start_command")
    if not start:
        return False
    cmd = ["bash", "-lc", str(Path(str(start)).expanduser())]
    if shutil.which("systemd-run"):
        # its own scope: Ollama must not belong to (and die with) the launcher's service
        cmd = ["systemd-run", "--user", "--scope", "--quiet", f"--unit=goblin-ollama-{int(time.time())}"] + cmd
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            p.loaded()
            return True
        except OSError:
            time.sleep(1)
    return False


def make_exclusive(cfg, model: str) -> list[str]:
    """Start the GPU Ollama if needed, then unload every model on it except `model`."""
    if not (cfg.get("gpu") or {}).get("exclusive", True):
        return []
    p = provider(cfg)
    if p is None or not ensure_up(cfg):
        return []
    try:
        others = [n for n in p.loaded() if _norm(n) != _norm(model)]
    except OSError:
        return []
    done = []
    for n in others:
        try:
            p.set_resident(n, 0)
            done.append(n)
        except (OSError, ProviderError):
            pass
    return done


def load(cfg, model: str, keep_alive: str = "30m") -> list[str]:
    """Load `model` alone on the GPU (the Furnace dial). Returns what was unloaded."""
    unloaded = make_exclusive(cfg, model)
    provider(cfg).set_resident(model, keep_alive)
    return unloaded


def unload_all(cfg) -> list[str]:
    p = provider(cfg)
    if p is None:
        return []
    try:
        names = p.loaded()
    except OSError:
        return []
    for n in names:
        try:
            p.set_resident(n, 0)
        except (OSError, ProviderError):
            pass
    return names
