"""The stack levers: everything around the agents, on and off (no sudo, no password prompts).

config `stack:`
  units: [openclaw-gateway.service, ...]       user units switched off/on (and disabled while off)
  webui: {url: http://127.0.0.1:3000/, container: open-webui}
  watch: {OpenClaw: 18789}                       ports shown as lights
OFF: stop + disable the units, stop the WebUI container, unload every model, stop the GPU Ollama.
ON:  the reverse. Left alone: system services, Goblin Launcher itself, the vault.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import gpu
from .providers import make


def _run(cmd: list[str], timeout: float = 45) -> tuple[int, str]:
    env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out: {' '.join(cmd)}"


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except OSError:
        return False


def _ollamas(cfg) -> dict:
    return {n: make(n, p) for n, p in (cfg.get("providers") or {}).items() if p.get("type") == "ollama"}


def _sc(cfg) -> dict:
    return cfg.get("stack") or {}


def _state_file(cfg) -> Path:
    return cfg.state / "stack.json"


def power(cfg) -> str:
    try:
        return json.loads(_state_file(cfg).read_text()).get("power", "on")
    except (OSError, ValueError):
        return "on"


def _set_power(cfg, value: str) -> None:
    _state_file(cfg).write_text(json.dumps({"power": value, "at": time.time()}))


def status(cfg) -> dict:
    models = {}
    for name, p in _ollamas(cfg).items():
        try:
            models[name] = p.loaded()
        except OSError:
            models[name] = None
    gpu_name = (cfg.get("gpu") or {}).get("provider")
    webui = (_sc(cfg).get("webui") or {}).get("url")
    up = False
    if webui:
        try:
            with urllib.request.urlopen(webui, timeout=1.5) as r:
                up = r.status < 500
        except OSError:
            up = False
    return {"power": power(cfg), "models": models,
            "ollama_gpu": models.get(gpu_name) is not None,
            "ollama_other": {n: m is not None for n, m in models.items() if n != gpu_name},
            "webui": up, "webui_url": webui,
            "watch": {label: port_open(port) for label, port in (_sc(cfg).get("watch") or {}).items()},
            "units": {u: _run(["systemctl", "--user", "is-active", u], 10)[1] == "active" for u in _sc(cfg).get("units") or []}}


def unload_all(cfg) -> str:
    notes = []
    for name, p in _ollamas(cfg).items():
        try:
            names = p.loaded()
        except OSError:
            continue
        for n in names:
            try:
                p.set_resident(n, 0)
            except Exception:  # noqa: BLE001
                pass
        notes.append(f"{name}: unloaded {len(names)}")
    return "; ".join(notes) or "no Ollama running"


def start_ollama(cfg) -> str:
    return "GPU Ollama up" if gpu.ensure_up(cfg) else "GPU Ollama didn't start (check providers.<gpu>.start_command)"


def stop_ollama(cfg) -> str:
    name = (cfg.get("gpu") or {}).get("provider")
    pc = (cfg.get("providers") or {}).get(name) or {}
    if pc.get("stop_command"):
        code, out = _run(["bash", "-lc", str(Path(str(pc["stop_command"])).expanduser())])
        return "GPU Ollama stopped" if code == 0 else f"stop: {out or code}"
    port = urllib.parse.urlparse(pc.get("base_url") or "").port
    if port and port_open(port) and shutil.which("fuser"):
        _run(["fuser", "-k", f"{port}/tcp"])
        return f"GPU Ollama on :{port} stopped"
    return "GPU Ollama already down"


def _webui(cfg, action: str) -> str:
    c = (_sc(cfg).get("webui") or {}).get("container")
    if not c:
        return "no WebUI container configured"
    if not shutil.which("docker"):
        return "docker not installed"
    code, out = _run(["docker", action, c], 60)
    return f"{c} {action}: {'ok' if code == 0 else out or code}"


def power_off(cfg) -> list[str]:
    log = []
    for u in _sc(cfg).get("units") or []:
        code, out = _run(["systemctl", "--user", "disable", "--now", u])
        log.append(f"{u}: {'off' if code == 0 else out or code}")
    log += [_webui(cfg, "stop"), unload_all(cfg), stop_ollama(cfg)]
    _set_power(cfg, "off")
    return log


def power_on(cfg) -> list[str]:
    log = []
    for u in _sc(cfg).get("units") or []:
        code, out = _run(["systemctl", "--user", "enable", "--now", u])
        log.append(f"{u}: {'on' if code == 0 else out or code}")
    log += [_webui(cfg, "start"), start_ollama(cfg)]
    _set_power(cfg, "on")
    return log
