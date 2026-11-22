"""Hook handler for Claude Code and Grok Build:  python -m goblin.agents.hook <claude|grok>

Rules: inert outside our launches (GOBLIN_LAUNCH=1); never blocks or decides permissions; fails
soft and fast (service down -> no output, the agent carries on).
  UserPromptSubmit  claude: librarian context as additionalContext
                    grok:   Grok drops that context, so the service holds it for the next tool result
  PostToolUse       trace the call; grok: deliver the held context next to the first result
  SessionEnd        close the trace session
"""
from __future__ import annotations

import json
import os
import sys

from ..sockclient import call

ALIASES = {"user_prompt_submit": "UserPromptSubmit", "post_tool_use": "PostToolUse", "session_end": "SessionEnd"}


def _say(event: str, context: str) -> None:
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}))


def _text(resp) -> str:
    if isinstance(resp, dict):
        parts = [str(resp[k]) for k in ("stdout", "stderr", "output", "content", "result", "error") if resp.get(k)]
        return "\n".join(parts) if parts else json.dumps(resp)[:20000]
    return resp if isinstance(resp, str) else json.dumps(resp)[:20000] if resp is not None else ""


def _exit(resp) -> int:
    if isinstance(resp, dict):
        for k in ("exit_code", "exitCode", "returncode", "code"):
            if isinstance(resp.get(k), int):
                return resp[k]
        if resp.get("is_error") or resp.get("isError") or resp.get("interrupted"):
            return 1
    return 0


def handle(agent: str, data: dict) -> None:
    event = data.get("hook_event_name") or ALIASES.get(os.environ.get("GROK_HOOK_EVENT", ""), "")
    session = str(data.get("session_id") or os.environ.get("GROK_SESSION_ID") or "default")
    who = os.environ.get("GOBLIN_AGENT") or agent
    if event == "UserPromptSubmit":
        body = {"prompt": str(data.get("prompt") or ""), "agent": who, "session": session, "defer": agent == "grok"}
        r = call("POST", "/v1/context", body, timeout=25)
        if agent != "grok" and r:
            _say(event, r.get("text") or "")
    elif event == "PostToolUse":
        resp = data.get("tool_response", data.get("tool_result"))
        r = call("POST", "/v1/trace", {"agent": who, "session": session, "tool": data.get("tool_name") or "?",
                                       "args": data.get("tool_input") or {}, "exit": _exit(resp), "output": _text(resp),
                                       "take_pending": agent == "grok"}, timeout=10)
        if r and r.get("pending"):
            _say(event, r["pending"])
    elif event == "SessionEnd":
        call("POST", "/v1/end", {"agent": who, "session": session, "status": str(data.get("reason") or "ended")},
             timeout=10)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get("GOBLIN_LAUNCH") != "1":
        return 0
    try:
        handle((argv[0] if argv else "agent").lower(), json.loads(sys.stdin.read() or "{}"))
    except Exception:  # noqa: BLE001 - a hook must never break the agent
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
