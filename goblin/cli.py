"""`goblin`: Goblin Launcher's command line.

  goblin open                       open the launcher page (starts goblin.service if needed)
  goblin serve                      run the launcher (systemd's goblin.service does this)
  goblin room read [N] | post TEXT | wait [SECONDS]      the phone bridge, zero model tokens
  goblin vault init | status | unlock | lock | extend | approvals | approve ID | deny ID
  goblin agents                     list launchable agents     goblin launch AGENT_ID [--cwd DIR]
  goblin mcp --agent NAME           MCP server for Grok Build / Claude Code / others
  goblin bridge-wake                wake CLI agents for phone-bridge posts (goblin-bridge-wake.service)
  goblin phone setup                guided: install the app on a phone over USB and pair it
  goblin librarian ...              Path Librarian: cards, shelf, report, mode, models (see --help)
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
import urllib.parse

from . import config


def _show(m: dict) -> None:
    print(f"[{(m.get('ts') or '')[11:16]}] {m.get('author_label') or m.get('author_id')}: {(m.get('text') or '').strip()}\n")


def room_cmd(cfg, args: list[str]) -> int:
    from .bridge import request
    cmd = args[0] if args else "read"
    try:
        if cmd == "read":
            for m in (request(cfg, "GET", "/api/bridge").get("messages") or [])[-int(args[1] if len(args) > 1 else 10):]:
                _show(m)
            return 0
        if cmd == "post":
            text = " ".join(args[1:]).strip() or sys.stdin.read().strip()
            if not text:
                print("nothing to post")
                return 2
            author = (cfg.get("bridge") or {}).get("terminal_author") or next(iter((cfg.get("bridge") or {}).get("authors") or {}), "")
            r = request(cfg, "POST", "/api/bridge/message", {"author_id": author, "text": text,
                                                             "client_nonce": f"term-{time.time_ns()}"})
            print(f"posted ({r.get('count')} messages in the room)")
            return 0
        if cmd == "wait":
            secs = args[1] if len(args) > 1 else "45"
            msgs = request(cfg, "GET", "/api/bridge").get("messages") or []
            after = msgs[-1]["id"] if msgs else ""
            r = request(cfg, "GET", f"/api/bridge/wait?after_id={urllib.parse.quote(after)}&timeout_s={secs}",
                        timeout=float(secs) + 10)
            for m in r.get("messages") or []:
                _show(m)
            if r.get("timed_out"):
                print("(no reply yet)")
            return 0
    except OSError as e:
        print(f"the room isn't reachable: {e}")
        return 1
    print("usage: goblin room [read [N] | post TEXT | wait [SECONDS]]")
    return 2


def vault_cmd(cfg, args: list[str]) -> int:
    from .vault import client
    kw = {"sock": (cfg.get("vault") or {}).get("socket"), "token": client.main_token(cfg.path("vault", "token_file"))}
    cmd = args[0] if args else "status"

    def done(code, d, ok_text):
        print(ok_text if code == 200 else f"no: {d.get('error') or code}")
        return 0 if code == 200 else 1

    if cmd == "status":
        code, s = client.call("GET", "/status", **kw)
        if code != 200:
            print(f"vault not reachable: {s.get('error')}")
            return 1
        left = s.get("seconds_remaining") or 0
        print(f"vault: {'UNLOCKED' if s.get('unlocked') else 'locked'}" + (f"  ({left // 60}m left)" if s.get("unlocked") else ""))
        _, a = client.call("GET", "/approvals", **kw)
        print(f"waiting approvals: {len(a.get('pending') or [])}")
        return 0
    if cmd == "init":
        a = getpass.getpass("New vault PIN (6-12 digits): ")
        if a != getpass.getpass("Again: "):
            print("they don't match; nothing changed")
            return 1
        return done(*client.call("POST", "/init", {"pin": a}, **kw), "vault created and open: keep that PIN safe")
    if cmd == "unlock":
        return done(*client.call("POST", "/unlock", {"pin": getpass.getpass("Vault PIN: ")}, **kw), "unlocked")
    if cmd == "lock":
        return done(*client.call("POST", "/lock", **kw), "locked")
    if cmd == "extend":
        return done(*client.call("POST", "/extend", {"minutes": 15}, pin=getpass.getpass("Vault PIN: "), **kw), "extended")
    if cmd == "approvals":
        code, d = client.call("GET", "/approvals", **kw)
        for i, p in enumerate(d.get("pending") or [], 1):
            print(f"{i}) {p['agent']} wants {p['target_id']}: {p['summary']}   [{p['id']}]")
        if code == 200 and not d.get("pending"):
            print("nothing waiting")
        return 0 if code == 200 else 1
    if cmd in ("approve", "deny") and len(args) > 1:
        pin = getpass.getpass("Vault PIN to approve: ") if cmd == "approve" else ""
        code, d = client.call("POST", f"/approvals/{args[1]}", {"approve": cmd == "approve"}, pin=pin, **kw)
        return done(code, d, d.get("decision", ""))
    print("usage: goblin vault [init|status|unlock|lock|extend|approvals|approve ID|deny ID]")
    return 2


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cfg = config.load()
    cmd, rest = argv[0], argv[1:]
    if cmd == "room":
        return room_cmd(cfg, rest)
    if cmd == "librarian":
        from .librarian.cli import main as librarian_main
        return librarian_main(rest)
    if cmd == "vault":
        return vault_cmd(cfg, rest)
    if cmd == "mcp":
        from .agents import mcp
        ap = argparse.ArgumentParser(prog="goblin mcp")
        ap.add_argument("--agent", default="mcp")
        mcp.main(ap.parse_args(rest).agent)
        return 0
    if cmd == "bridge-wake":
        from .bridge import wake_main
        return wake_main()
    if cmd == "agents":
        from .agents import cli
        for a in cli.list_agents(cfg):
            print(f"{a['id']:32} {'local' if a['local'] else 'cloud':6} {'' if a['available'] else '(not installed)'}")
        return 0
    if cmd == "launch":
        from .agents import cli
        ap = argparse.ArgumentParser(prog="goblin launch")
        ap.add_argument("agent")
        ap.add_argument("--cwd")
        a = ap.parse_args(rest)
        print(json.dumps(cli.launch(cfg, a.agent, a.cwd)))
        return 0
    if cmd == "open":
        import subprocess
        import urllib.request
        ui = cfg.get("ui") or {}
        url = f"http://{ui.get('host', '127.0.0.1')}:{ui.get('port', 8770)}/"

        def up():
            try:
                urllib.request.urlopen(url, timeout=1).close()
                return True
            except OSError:
                return False
        if not up():
            subprocess.run(["systemctl", "--user", "start", "goblin.service"], check=False)
            for _ in range(60):
                if up():
                    break
                time.sleep(0.2)
        return subprocess.call(["xdg-open", url])
    if cmd == "serve":
        from .service import main as serve_main
        return serve_main(cfg, rest)
    if cmd == "phone":
        from .phone_setup import main as phone_main
        return phone_main(cfg, rest)
    print(f"unknown command {cmd!r}\n\n{__doc__.strip()}")
    return 2
