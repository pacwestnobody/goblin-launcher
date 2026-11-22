"""The phone bridge from outside: the `goblin-bridge` command for bots, and the wake loop.

  goblin-bridge post AUTHOR [NONCE] < text     goblin-bridge read [AFTER_ID]
  goblin-bridge wait AFTER_ID [SECONDS]        goblin-bridge up

Wake loop (goblin-bridge-wake.service), config `bridge.wake`:
  authors: [ids]        posts by these wake an agent: the one(s) the text names, else `default`
  agents: {grok: {author: grok-build}, claude: {author: claude-code}}
  max_hops: 4           agent-to-agent hand-offs ("@claude" / "Claude," at a line start) per human post
Each message is marked seen BEFORE an agent runs, so an agent that reboots the machine can't be
woken again by the same message after boot. First run marks history seen without replaying it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import config
from .agents import cli


def _base(cfg) -> str:
    rc = cfg.get("room") or {}
    return f"http://{rc.get('host', '127.0.0.1')}:{rc.get('port', 8765)}"


def request(cfg, method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    req = urllib.request.Request(_base(cfg) + path, method=method, headers={"Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def up(cfg) -> bool:
    try:
        request(cfg, "GET", "/api/status", timeout=3)
        return True
    except (OSError, ValueError):
        return False


def ensure_up(cfg) -> None:
    if up(cfg):
        return
    subprocess.run(["systemctl", "--user", "start", "goblin.service"], check=False, timeout=30)
    for _ in range(60):
        if up(cfg):
            return
        time.sleep(0.5)
    raise SystemExit("Goblin Launcher's room isn't answering")


def post(cfg, author: str, text: str, nonce: str = "") -> dict:
    return request(cfg, "POST", "/api/bridge/message",
                   {"author_id": author, "text": text, **({"client_nonce": nonce} if nonce else {})}, timeout=30)


def cli_main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.split("Wake loop")[0].strip())
        return 0
    cfg = config.load()
    ensure_up(cfg)
    cmd = argv[0]
    try:
        if cmd == "up":
            out = request(cfg, "GET", "/api/status", timeout=10)
        elif cmd == "post":
            out = post(cfg, argv[1] if len(argv) > 1 else "", sys.stdin.read(), argv[2] if len(argv) > 2 else "")
        elif cmd == "read":
            out = request(cfg, "GET", "/api/bridge" + (f"?after_id={urllib.parse.quote(argv[1])}" if len(argv) > 1 else ""))
        elif cmd == "wait":
            secs = argv[2] if len(argv) > 2 else "25"
            out = request(cfg, "GET", f"/api/bridge/wait?after_id={urllib.parse.quote(argv[1])}&timeout_s={secs}",
                          timeout=float(secs) + 10)
        else:
            raise SystemExit(f"unknown command {cmd}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read().decode(errors='replace')}") from None
    print(json.dumps(out, indent=2))
    return 0


# ---- wake loop ---------------------------------------------------------------------------
class Wake:
    def __init__(self, cfg):
        self.cfg = cfg
        w = (cfg.get("bridge") or {}).get("wake") or {}
        self.wake_authors = set(w.get("authors") or [])
        self.agents = w.get("agents") or {"grok": {"author": "grok-build"}, "claude": {"author": "claude-code"}}
        self.default = w.get("default") or "grok"
        self.max_hops = int(w.get("max_hops", 4))
        self.notes = w.get("notes") or ""
        self.dir = cfg.state / "bridge-wake"
        self.runs = self.dir / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        try:
            self.state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self.state = {"seen": [], "last_id": ""}

    def save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        tmp.replace(self.state_file)

    def mark(self, mid: str) -> None:
        self.state["seen"] = sorted(set(self.state.get("seen") or []) | {mid})[-5000:]
        self.state["last_id"] = mid
        self.save()

    def seed(self) -> None:
        msgs = request(self.cfg, "GET", "/api/bridge", timeout=30).get("messages") or []
        self.state = {"seen": [m["id"] for m in msgs if m.get("id")], "last_id": msgs[-1]["id"] if msgs else ""}
        self.save()

    def route(self, msg: dict) -> list[str]:
        author, text = msg.get("author_id") or "", msg.get("text") or ""
        agent_authors = {a["author"]: name for name, a in self.agents.items()}
        if author in self.wake_authors:
            self.state["hops"] = 0
            named = [n for n in self.agents if re.search(rf"\b{re.escape(n)}\b", text, re.I)]
            return named or [self.default]
        if author in agent_authors and int(self.state.get("hops") or 0) < self.max_hops:
            for other in self.agents:
                if other != agent_authors[author] and re.search(rf"(?:@{other}\b|(?:^|\n)\s*{other}[,:])", text, re.I):
                    self.state["hops"] = int(self.state.get("hops") or 0) + 1
                    return [other]
        return []

    def prompt(self, name: str, msg: dict) -> str:
        me = self.agents[name]["author"]
        others = [f'"@{n}"' for n in self.agents if n != name]
        exe = cfg_bin()
        return (f"You were woken headlessly on {self.cfg.get('machine')} because {msg.get('author_id')} posted in "
                f"the phone bridge room ({self.cfg.get('owner')} drives this machine from the phone through it). "
                f"Nobody is at the keyboard. Do the job on this machine with your tools.\n\n"
                f"Message id: {msg.get('id')}\nAuthor: {msg.get('author_id')}\nPosted at: {msg.get('ts')}\n\n"
                f"Message:\n{msg.get('text')}\n\n"
                f"Post progress on long jobs and the result into the room as {me}:\n\n"
                f"{exe} post {me} <<'EOF2'\nresult here\nEOF2\n\n"
                f"Cross-talk: other agents also answer this room; to hand one something, start a line with "
                f"{' or '.join(others)}. Read earlier context with `{exe} read` only if the message depends on it. "
                f"If you can't finish, post what's done and what's left. {self.notes}").strip()

    def run(self, name: str, msg: dict) -> None:
        mid = msg["id"]
        which = "claude" if name == "claude" else "grok"
        session, first = None, True
        if which == "claude":              # one Claude session across bridge messages: it remembers them
            session = self.state.get("claude_session")
            first = not session
            if first:
                session = self.state["claude_session"] = str(uuid.uuid4())
                self.save()
        log = self.runs / f"{mid}.{name}.log"
        print(f"waking {name} for {mid}", flush=True)
        code, _ = cli.headless(self.cfg, which, self.prompt(name, msg), agent_id=f"bridge:{name}", session=session,
                               first=first, log_path=log, timeout=3600,
                               extra=[str(a) for a in self.agents[name].get("args") or []])
        print(f"{name} {mid} exit {code} log {log}", flush=True)
        if code != 0:
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-8:])[:800] if log.is_file() else ""
            try:
                post(self.cfg, self.agents[name]["author"], f"Headless {name} exited {code} on message {mid}. Log: {log}\n{tail}")
            except (OSError, ValueError):
                pass

    def loop(self) -> None:
        ensure_up(self.cfg)
        if not self.state.get("last_id"):
            self.seed()
        print(f"waiting after {self.state.get('last_id') or 'nothing'} ({len(self.state.get('seen') or [])} seen)", flush=True)
        while True:
            try:
                view = request(self.cfg, "GET", f"/api/bridge/wait?after_id={urllib.parse.quote(self.state['last_id'])}"
                               "&timeout_s=25", timeout=40)
            except (OSError, ValueError) as e:
                print(f"bridge unreachable: {e}", flush=True)
                time.sleep(5)
                continue
            if view.get("resync"):
                print("bridge cursor missing; marking history seen", flush=True)
                self.seed()
                continue
            seen = set(self.state.get("seen") or [])
            for msg in view.get("messages") or []:
                if not msg.get("id") or msg["id"] in seen:
                    continue
                who = self.route(msg)
                self.mark(msg["id"])          # before acting
                seen.add(msg["id"])
                for name in who:
                    self.run(name, msg)


def cfg_bin() -> str:
    from pathlib import Path
    return str(Path.home() / ".local" / "bin" / "goblin-bridge")


def wake_main() -> int:
    cfg = config.load()
    if not ((cfg.get("bridge") or {}).get("wake") or {}).get("enabled"):
        print("bridge wake is off in this config (bridge.wake.enabled); not waking anyone", flush=True)
        return 0
    Wake(cfg).loop()
    return 0
