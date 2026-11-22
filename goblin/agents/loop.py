"""A seat's turn: model call -> tool calls -> results -> ... -> written answer.

History is kept in one internal shape (OpenAI-style roles; assistant tool calls as
{"id", "name", "arguments": dict}); each provider converts it to its own wire format.
Tool results going back into the conversation are capped so the reply keeps room to be written.
If the round budget runs out, one last no-tools call asks for the report from what was gathered.
"""
from __future__ import annotations

import json
import re
import uuid

from .. import lab
from ..providers import ProviderError, make
from .tools import SPECS, Tools

LOOKUP = {"type": "function", "function": {
    "name": "librarian_lookup",
    "description": "Verified facts about the lab and notes from the knowledge library for a question "
                   "(IPs, VMIDs, hosts, bridges, past fixes). Use before guessing.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}


class NoLibrarian:
    """Stand-in when the Path Librarian isn't attached: no context, no traces."""

    def context(self, prompt: str, agent: str, session: str) -> str:
        return ""

    def trace(self, agent: str, session: str, tool: str, args: dict, exit_code, output: str) -> None:
        pass

    def end(self, agent: str, session: str, status: str) -> None:
        pass


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _exit_of(name: str, out: str):
    m = re.match(r"exit=(-?\d+)", out or "") if name == "run_terminal_command" else None
    return int(m.group(1)) if m else (1 if (out or "").startswith("ERROR:") else 0)


def run_turn(cfg, seat: dict, messages: list[dict], librarian=None, room_id: str = "room") -> str:
    librarian = librarian or NoLibrarian()
    prov = make(seat["provider"], (cfg.get("providers") or {}).get(seat["provider"]) or {})
    session = f"room:{room_id}:{seat['id']}:{uuid.uuid4().hex[:8]}"
    status = "error"
    try:
        history = [dict(m) for m in messages]
        try:
            ctx = librarian.context(_last_user(history), seat["id"], session)
        except Exception:  # noqa: BLE001 - a turn still runs without the librarian
            ctx = ""
        if ctx and history and history[0]["role"] == "system":
            history[0]["content"] += "\n\n" + ctx
        text = _loop(cfg, seat, prov, history, librarian, session)
        status = "answered"
        return text
    finally:
        try:
            librarian.end(seat["id"], session, status)
        except Exception:  # noqa: BLE001
            pass


def _loop(cfg, seat, prov, history, librarian, session) -> str:
    room = cfg.get("room") or {}
    max_tokens = int(seat.get("max_tokens") or 2048)
    if not seat.get("tools", True):
        return prov.chat(seat["model"], history, None, max_tokens).content or "(empty)"
    tools = Tools(cfg, seat)
    specs = SPECS + [LOOKUP] + ([lab.spec(cfg)] if seat.get("lab", True) else [])
    gathered: list[str] = []
    cap = int(room.get("tool_result_cap", 4000))
    for _ in range(int(room.get("max_rounds", 20))):
        reply = prov.chat(seat["model"], history, specs, max_tokens)
        if not reply.tool_calls:
            return reply.content or "(empty response)"
        history.append({"role": "assistant", "content": reply.content, "tool_calls": reply.tool_calls})
        for tc in reply.tool_calls:
            name, args = tc["name"] or "?", tc.get("arguments") or {}
            if name == "lab_run":
                code, out = lab.run(cfg, str(args.get("target") or ""), str(args.get("command") or ""),
                                    agent=f"room:{seat['id']}")
                out = f"exit={code}\n{out}"
            elif name == "librarian_lookup":
                out = librarian.context(str(args.get("query") or ""), seat["id"], session) or \
                    "Nothing relevant in the verified facts or the knowledge library."
            else:
                out = tools.run(name, args)
            try:
                librarian.trace(seat["id"], session, name, args, _exit_of(name, out), out)
            except Exception:  # noqa: BLE001 - tracing never breaks a turn
                pass
            gathered.append(f"{name}({json.dumps(args)[:100]}): {out[:200].replace(chr(10), ' ')}")
            kept = out if len(out) <= cap else out[:cap] + "\n[cut so the reply still has room; read a smaller range]"
            history.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": kept})
    try:
        final = prov.chat(seat["model"], history + [{"role": "user", "content": (
            "Tool budget used up. Stop calling tools and write the report now from the results above. "
            "Say what you verified and flag anything unconfirmed.")}], None, max_tokens)
        if final.content:
            return final.content
    except ProviderError as e:
        gathered.append(f"(final write-up failed: {e})")
    return "Tool budget used up before a final answer. Gathered:\n" + "\n".join(f"- {g}" for g in gathered)
