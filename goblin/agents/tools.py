"""Real local tools for room seats: shell, read, list, grep, write, replace.

Guarded seats (seat config `guard: true`) get extra limits from config `guard:`:
  knowledge_via_lookup: true      lab facts come from librarian_lookup, not grepping the notes
  read_only: [paths]              may read, never write (e.g. core-facts.md)
  no_access: [paths]              neither (e.g. the librarian's index)
A soft denylist blocks obvious accidents (rm -rf /, mkfs, fork bombs, reboot) for every seat.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

HOME = Path.home()
MAX_OUT = 14_000

_DENY = re.compile(r"(rm\s+-rf\s+/(\s|$)|mkfs\.|dd\s+if=.*of=/dev/|:\(\)\s*\{|curl\s+[^\n]*\|\s*(ba)?sh|"
                   r"\bshutdown\b|\breboot\b|\bhalt\b|\buserdel\b|\bpasswd\s)", re.I)
_WRITES = re.compile(r"(>>?|\btee\b|\bmv\b|\bcp\b|\brm\b|\bsed\s+-i|\btruncate\b)")


def _fn(name, description, props, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


SPECS = [
    _fn("run_terminal_command", "Run a bash command on this machine. Prefer non-interactive commands; verify results.",
        {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}, ["command"]),
    _fn("read_file", "Read a text file (numbered lines).",
        {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
    _fn("list_dir", "List a directory.", {"path": {"type": "string"}}, ["path"]),
    _fn("grep", "Search file contents (ripgrep when installed).",
        {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, ["pattern", "path"]),
    _fn("write_file", "Create or overwrite a text file.", {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"]),
    _fn("search_replace", "Replace one exact, unique piece of text in a file.",
        {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}},
        ["path", "old_string", "new_string"]),
]


def clip(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n…[{len(s) - n} chars cut]…\n" + s[-n // 2:]


def _resolve(path: str) -> Path:
    p = Path(str(path or ".")).expanduser()
    return (p if p.is_absolute() else HOME / p).resolve()


class Tools:
    def __init__(self, cfg, seat: dict):
        self.cfg, self.seat = cfg, seat
        g = cfg.get("guard") or {}
        self.guarded = bool(seat.get("guard"))
        self.knowledge = str(cfg.path("paths", "knowledge"))
        self.via_lookup = bool(g.get("knowledge_via_lookup", True))
        self.read_only = [str(Path(p).expanduser()) for p in g.get("read_only") or []]
        self.no_access = [str(Path(p).expanduser()) for p in g.get("no_access") or []]

    # ---- guards ---------------------------------------------------------------
    def _denied(self, text: str, write: bool) -> str | None:
        if not self.guarded:
            return None
        t = text.replace("\\", "/")
        if any(p in t or Path(p).name in t for p in self.no_access):
            return "that file is the librarian's only"
        if write and any(p in t or Path(p).name in t for p in self.read_only):
            return "that file is read-only from this seat"
        if self.via_lookup and re.search(r"\b(grep|rg)\b", t) and (self.knowledge in t or "~/knowledge" in t):
            return "for lab facts and the notes library call librarian_lookup"
        return None

    def run(self, name: str, args: dict) -> str:
        try:
            if name == "run_terminal_command":
                cmd = str(args.get("command") or "")
                why = self._denied(cmd, write=bool(_WRITES.search(cmd)))
                return f"ERROR: denied: {why}" if why else self._shell(args)
            path = str(args.get("path") or "")
            if name == "grep" and self.guarded and self.via_lookup:
                if path.strip() in ("", ".", "~", str(HOME)) or str(_resolve(path)).startswith(self.knowledge):
                    return "ERROR: denied: for lab facts and the notes library call librarian_lookup"
            why = self._denied(path, write=name in ("write_file", "search_replace"))
            if why:
                return f"ERROR: denied: {why}"
            fn = {"read_file": self._read, "list_dir": self._list, "grep": self._grep,
                  "write_file": self._write, "search_replace": self._replace}.get(name)
            return fn(args) if fn else f"ERROR: unknown tool {name}"
        except Exception as e:  # noqa: BLE001 - a tool error is a result, not a crash
            return f"ERROR: {type(e).__name__}: {e}"

    # ---- tools ----------------------------------------------------------------
    def _shell(self, args: dict) -> str:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return "ERROR: empty command"
        if _DENY.search(cmd):
            return "ERROR: blocked by the room's safety list"
        env = {**os.environ, "PATH": f"{HOME / '.local/bin'}:{os.environ.get('PATH', '')}"}
        for p in reversed(self.seat.get("path_prepend") or []):
            env["PATH"] = f"{Path(p).expanduser()}:{env['PATH']}"
        env.update({k: str(Path(v).expanduser()) if str(v).startswith("~") else str(v)
                    for k, v in (self.seat.get("env") or {}).items()})
        if env.get("SUDO_ASKPASS"):      # no TTY in the room: sudo asks through a desktop dialog
            cmd = re.sub(r"(^|[;&|]\s*)sudo(?!\s+-A\b)\b", r"\1sudo -A", cmd)
        cwd = _resolve(args.get("cwd") or self.seat.get("cwd") or HOME)
        if not cwd.is_dir():
            return f"ERROR: not a directory: {cwd}"
        limit = int((self.cfg.get("room") or {}).get("shell_timeout", 180))
        timeout = max(1, min(int(args.get("timeout") or 120), limit))
        try:
            p = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return f"ERROR: timeout after {timeout}s"
        return clip(f"exit={p.returncode}\n{p.stdout}{p.stderr}")

    def _read(self, args: dict) -> str:
        path = _resolve(args["path"])
        if not path.is_file():
            return f"ERROR: not a file: {path}"
        if path.stat().st_size > 2_000_000:
            return f"ERROR: file too large ({path.stat().st_size} bytes)"
        lines = path.read_text(errors="replace").splitlines()
        start = max(1, int(args.get("offset") or 1)) - 1
        chunk = lines[start:start + int(args.get("limit") or 400)]
        return clip("\n".join(f"{i + start + 1}|{l}" for i, l in enumerate(chunk)) or "(empty)")

    def _list(self, args: dict) -> str:
        path = _resolve(args["path"])
        if not path.is_dir():
            return f"ERROR: not a directory: {path}"
        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"{'dir' if e.is_dir() else 'file'}\t{e.name}" for e in items[:500]]
        return "\n".join(lines + ([f"… +{len(items) - 500} more"] if len(items) > 500 else [])) or "(empty)"

    def _grep(self, args: dict) -> str:
        pattern, path = str(args.get("pattern") or ""), _resolve(args.get("path"))
        if not pattern:
            return "ERROR: empty pattern"
        if shutil.which("rg"):
            cmd = ["rg", "-n", "--no-heading", "-m", "80", pattern, str(path)]
            if args.get("glob"):
                cmd += ["--glob", str(args["glob"])]
        else:
            cmd = ["grep", "-RIn", "-m", "80", "--", pattern, str(path)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return "ERROR: grep timed out"
        return clip((p.stdout + p.stderr) or "(no matches)")

    def _write(self, args: dict) -> str:
        path = _resolve(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content") or ""))
        return f"OK wrote {path} ({len(str(args.get('content') or ''))} chars)"

    def _replace(self, args: dict) -> str:
        path = _resolve(args["path"])
        if not path.is_file():
            return f"ERROR: not a file: {path}"
        text, old = path.read_text(errors="replace"), str(args.get("old_string"))
        n = text.count(old)
        if n != 1:
            return "ERROR: old_string not found" if n == 0 else f"ERROR: old_string found {n} times; make it unique"
        path.write_text(text.replace(old, str(args.get("new_string")), 1))
        return f"OK replaced once in {path}"
