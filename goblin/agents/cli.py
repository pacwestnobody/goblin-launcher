"""Grok Build and Claude Code: terminal launches, headless room/bridge turns, and their wiring.

Wiring (nothing global changes for ordinary sessions):
  Grok Build   one hook file ~/.grok/hooks/goblin.json, inert unless GOBLIN_LAUNCH=1 (set only by
               us), because Grok has no per-launch hook option. Its MCP server entry
               [mcp_servers.goblin] in ~/.grok/config.toml is written by the installer.
  Claude Code  per-launch --settings (hooks) and --mcp-config files in the state dir.
Room seats run the real CLI headless with one session per room, so each remembers the room.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

HOME = Path.home()


def bin_of(cfg, which: str) -> Path:
    return Path(str((cfg.get("cli_agents") or {}).get(which, {}).get("bin") or "")).expanduser()


def grok_rules(cfg) -> str:
    return ((cfg.get("cli_agents") or {}).get("grok") or {}).get("rules") or (
        "Path Librarian is attached to this session. Verified lab facts and notes from the knowledge "
        "library may appear next to tool results; live tool output wins over notes. Never search for "
        "credentials, tokens or keys; if you lack access, say what is missing.")


# ---- wiring -------------------------------------------------------------------------
def _hook_cmd(agent: str) -> str:
    return f"{shlex.quote(sys.executable)} -m goblin.agents.hook {agent}"


def _hooks(agent: str) -> dict:
    entry = lambda t: [{"hooks": [{"type": "command", "command": _hook_cmd(agent), "timeout": t}]}]  # noqa: E731
    return {"hooks": {"UserPromptSubmit": entry(30), "PostToolUse": entry(15), "SessionEnd": entry(10)}}


def ensure_grok_hook() -> Path:
    path = HOME / ".grok" / "hooks" / "goblin.json"
    want = json.dumps(_hooks("grok"), indent=2) + "\n"
    if not path.is_file() or path.read_text() != want:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want)
    return path


def claude_files(cfg) -> tuple[Path, Path]:
    d = cfg.state / "agent-config"
    d.mkdir(parents=True, exist_ok=True)
    settings, mcp = d / "claude-settings.json", d / "claude-mcp.json"
    settings.write_text(json.dumps(_hooks("claude"), indent=2) + "\n")
    mcp.write_text(json.dumps({"mcpServers": {"goblin": {
        "command": sys.executable, "args": ["-m", "goblin", "mcp", "--agent", "claude-code"]}}}, indent=2) + "\n")
    return settings, mcp


def _env(cfg, agent_id: str, extra: dict | None = None) -> dict:
    return {**os.environ, "GOBLIN_LAUNCH": "1", "GOBLIN_AGENT": agent_id,
            "PATH": f"{HOME / '.local/bin'}:{os.environ.get('PATH', '')}", **(extra or {})}


# ---- headless runs -------------------------------------------------------------------
def headless(cfg, which: str, prompt: str, *, agent_id: str, session: str | None = None, first: bool = True,
             model: str | None = None, timeout: int = 1200, log_path: Path | None = None,
             extra: list[str] | None = None) -> tuple[int, str]:
    """Run Grok Build or Claude Code once, non-interactively, full tools. Returns (exit, output)."""
    env = _env(cfg, agent_id)
    if which == "claude":
        settings, mcp = claude_files(cfg)
        argv = [str(bin_of(cfg, "claude")), "-p", prompt, "--settings", str(settings), "--mcp-config", str(mcp),
                "--permission-mode", "bypassPermissions", *(extra or [])]
        if session:
            argv += ["--session-id", session] if first else ["--resume", session]
    else:
        ensure_grok_hook()
        model = model or (cfg.get("cli_agents") or {}).get("grok", {}).get("default_model") or "grok-4.7"
        env["GROK_DEFAULT_MODEL"] = model
        argv = [str(bin_of(cfg, "grok")), "-m", model, "--always-approve", "--rules", grok_rules(cfg), *(extra or [])]
        if session:
            argv += ["-s", session] if first else ["-r", session]
        argv += ["-p", prompt]
    try:
        if log_path:
            with open(log_path, "w") as fh:
                p = subprocess.run(argv, cwd=HOME, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=timeout)
            return p.returncode, ""
        p = subprocess.run(argv, cwd=HOME, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
        out = (p.stdout or "").strip()
        return p.returncode, out if out or p.returncode == 0 else (p.stderr or "").strip()[-600:]
    except subprocess.TimeoutExpired:
        return 124, f"stopped after {timeout // 60} minutes"
    except OSError as e:
        return 127, f"could not start: {e}"


def room_prompt(messages: list[dict], first: bool) -> str:
    """Only what's new since this seat last spoke, labelled; the whole brief on the first turn."""
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    convo = [m for m in messages if m.get("role") != "system"]
    last_own = max((i for i, m in enumerate(convo) if m.get("role") == "assistant"), default=-1)
    lines = []
    for m in convo[last_own + 1:]:
        text = str(m.get("content") or "").strip()
        if text:
            lines.append(text if text.startswith("[") and " said]:" in text.split("\n", 1)[0] else f"[Owner]:\n{text}")
    body = "\n\n".join(lines) or "[Owner]:\n(no new messages)"
    if first:
        return f"{system}\n\nYou are a seat in this room. Reply with what you want posted. Messages so far:\n\n{body}"
    return f"New in the room since your last reply:\n\n{body}"


def room_turn(cfg, seat: dict, messages: list[dict], room_id: str) -> str:
    which = "claude" if seat["provider"] == "claude-code" else "grok"
    f = cfg.state / "rooms" / f"{room_id}.cli-sessions.json"
    try:
        sessions = json.loads(f.read_text())
    except (OSError, ValueError):
        sessions = {}
    first = seat["id"] not in sessions
    if first:
        sessions[seat["id"]] = str(uuid.uuid4())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(sessions, indent=2))
    code, out = headless(cfg, which, room_prompt(messages, first), agent_id=f"room:{seat['id']}",
                         session=sessions[seat["id"]], first=first, model=seat.get("model"))
    if code != 0 and not out:
        return f"⚠️ {seat['label']} exited {code}"
    return out if code == 0 else f"⚠️ {seat['label']} exited {code}: {out}"


# ---- terminal launches ---------------------------------------------------------------
def list_agents(cfg) -> list[dict]:
    """Every model in Grok's config (local ones flagged) plus Claude Code."""
    grok = bin_of(cfg, "grok")
    try:
        gcfg = tomllib.loads((HOME / ".grok" / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        gcfg = {}

    def flat(tbl, prefix=""):          # [model.grok-4.5] parses as model -> grok-4 -> 5
        for k, v in tbl.items():
            if isinstance(v, dict):
                name = f"{prefix}.{k}" if prefix else k
                if any(not isinstance(x, dict) for x in v.values()):
                    yield name, v
                else:
                    yield from flat(v, name)

    out = []
    for mid, m in flat(gcfg.get("model") or {}):
        base = str(m.get("base_url") or "")
        local = base.startswith(("http://127.0.0.1", "http://localhost"))
        out.append({"id": f"grok:{mid}", "harness": "grok", "model": mid, "label": mid, "local": local,
                    "ollama_model": m.get("model") if local else None, "available": grok.exists()})
    default = (gcfg.get("models") or {}).get("default") or (cfg.get("cli_agents") or {}).get("grok", {}).get("default_model")
    if default and f"grok:{default}" not in {a["id"] for a in out}:
        out.append({"id": f"grok:{default}", "harness": "grok", "model": default, "label": default, "local": False,
                    "available": grok.exists()})
    out.sort(key=lambda a: (not a["local"], a["model"]))
    out.append({"id": "claude-code", "harness": "claude", "model": None, "label": "Claude Code", "local": False,
                "available": bin_of(cfg, "claude").exists()})
    return out


def launch(cfg, agent_id: str, cwd: str | None = None) -> dict:
    agent = next((a for a in list_agents(cfg) if a["id"] == agent_id), None)
    if not agent:
        raise ValueError(f"unknown agent {agent_id}")
    if not agent["available"]:
        raise ValueError(f"{agent['label']} is not installed")
    unloaded = []
    if agent.get("ollama_model"):
        from .. import gpu
        unloaded = gpu.make_exclusive(cfg, agent["ollama_model"])
    profiles = (cfg.get("cli_agents") or {}).get("grok", {}).get("profiles") or {}
    prof = profiles.get(agent.get("model") or "", {})
    work = Path(cwd or prof.get("cwd") or HOME).expanduser()
    env = {"GOBLIN_LAUNCH": "1", "GOBLIN_AGENT": agent_id, **(prof.get("env") or {})}
    lines = ["#!/usr/bin/env bash", "set +e", f'export PATH={shlex.quote(str(HOME / ".local/bin"))}:"$PATH"']
    for p in prof.get("path_prepend") or []:
        lines.append(f'export PATH={shlex.quote(str(Path(p).expanduser()))}:"$PATH"')
    if agent["harness"] == "grok":
        ensure_grok_hook()
        env["GROK_DEFAULT_MODEL"] = agent["model"]
        if agent["local"]:    # 98k local prefill needs far more than the cloud compaction budget
            env.update(GROK_COMPACTION_WALL_CLOCK_SECS="1500", GROK_COMPACTION_TOOL_CHOICE="none")
        cmd = f"{shlex.quote(str(bin_of(cfg, 'grok')))} -m {shlex.quote(agent['model'])} --rules {shlex.quote(grok_rules(cfg))}"
        lines.append("unset GROK_MODEL 2>/dev/null || true")
    else:
        settings, mcp = claude_files(cfg)
        cmd = f"{shlex.quote(str(bin_of(cfg, 'claude')))} --settings {shlex.quote(str(settings))} --mcp-config {shlex.quote(str(mcp))}"
    lines += [f"export {k}={shlex.quote(str(Path(v).expanduser()) if str(v).startswith('~') else str(v))}" for k, v in env.items()]
    lines += [f"cd {shlex.quote(str(work))} || cd ~", f'echo "Goblin Launcher: {agent["label"]}  (cwd: $(pwd))"', "echo",
              cmd, "rc=$?", 'echo; echo "exited with code $rc"', 'read -r -p "Press Enter to close..." _']
    d = cfg.state / "launches"
    d.mkdir(parents=True, exist_ok=True)
    script = d / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{agent_id.replace(':', '-').replace('/', '_')}.sh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o700)
    term = next((t for t in ("gnome-terminal", "ptyxis", "x-terminal-emulator", "kitty", "alacritty") if shutil.which(t)), None)
    if not term:
        raise RuntimeError("no terminal found (gnome-terminal, ptyxis, kitty, alacritty)")
    title = f"Goblin · {agent['label']}"[:60]
    argv = {"gnome-terminal": ["gnome-terminal", "--window", "-t", title, "--working-directory", str(work), "--"],
            "ptyxis": ["ptyxis", "--new-window", "-d", str(work), "-T", title, "--"]}.get(term, [term, "-e"])
    subprocess.Popen(argv + ["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "agent": agent_id, "terminal": term, "unloaded": unloaded}
