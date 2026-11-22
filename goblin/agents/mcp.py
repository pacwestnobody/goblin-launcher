"""`goblin mcp --agent NAME`: a small MCP stdio server for Grok Build, Claude Code and others.

Three tools (each description rides in every prompt, so they stay short):
  librarian_context  verified lab facts + knowledge-library notes for a question
  lab_run            a command on a lab host through the vault (the owner approves)
  librarian_shelf    only the facts whose live probes passed recently
"""
from __future__ import annotations

import json
import sys

from .. import config, lab
from ..sockclient import call

PROTOCOL = "2025-06-18"


def tools(cfg) -> list[dict]:
    spec = lab.spec(cfg)["function"]
    return [
        {"name": "librarian_context",
         "description": "Verified facts about the lab and notes from the knowledge library for a question. "
                        "Use before guessing IPs, VMIDs, hosts or past fixes.",
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
        {"name": "lab_run", "description": spec["description"], "inputSchema": spec["parameters"]},
        {"name": "librarian_shelf", "description": "Only the lab facts whose live probes passed recently.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def _text(t: str) -> dict:
    return {"content": [{"type": "text", "text": t}]}


def handle(cfg, msg: dict, agent: str, session: str):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "goblin", "version": "2"}}
    if method == "tools/list":
        return {"tools": tools(cfg)}
    if method == "ping":
        return {}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name == "librarian_context":
            r = call("POST", "/v1/context", {"prompt": str(args.get("query") or ""), "agent": agent, "session": session})
            return _text("Goblin Launcher is not running." if r is None
                         else r.get("text") or "Nothing relevant on the shelf or in the library.")
        if name == "lab_run":
            code, out = lab.run(cfg, str(args.get("target") or ""), str(args.get("command") or ""), agent=f"mcp:{agent}")
            return _text(f"exit={code}\n{out}")
        if name == "librarian_shelf":
            r = call("GET", "/v1/shelf")
            if r is None:
                return _text("Goblin Launcher is not running.")
            facts = [f"- {x['fact']}" for x in r.get("rows", []) if x.get("state") == "pass"]
            return _text("\n".join(facts) or "No verified facts right now.")
        raise LookupError(f"unknown tool {name}")
    if mid is None:
        return None
    raise NotImplementedError(f"method not found: {method}")


def main(agent: str = "mcp") -> None:
    cfg = config.load()
    session = f"mcp-{agent}"
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            result = handle(cfg, msg, agent, session)
            if msg.get("id") is None:
                continue
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32602, "message": str(e)}}
        except NotImplementedError as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
