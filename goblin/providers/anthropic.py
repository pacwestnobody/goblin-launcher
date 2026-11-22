"""Anthropic Messages API."""
from __future__ import annotations

from .base import Provider, Reply, post_json


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    system, conv = [], []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system.append(m.get("content") or "")
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "tool",
                     "content": m.get("content") or ""}
            if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                conv[-1]["content"].append(block)        # results of one turn go in one user message
            else:
                conv.append({"role": "user", "content": [block]})
        elif role == "assistant" and m.get("tool_calls"):
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments") or {}}
                       for tc in m["tool_calls"]]
            conv.append({"role": "assistant", "content": blocks})
        elif role in ("user", "assistant"):
            conv.append({"role": role, "content": m.get("content") or ""})
    return "\n\n".join(s for s in system if s), conv


class Anthropic(Provider):
    default_base = "https://api.anthropic.com"

    def chat(self, model, messages, tools=None, max_tokens=2048, temperature=0.4):
        system, conv = to_anthropic(messages)
        payload = {"model": model, "max_tokens": max_tokens, "messages": conv, "temperature": temperature}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                                 "input_schema": t["function"].get("parameters") or {"type": "object"}} for t in tools]
        data = post_json(self.base_url + "/v1/messages", payload,
                         {"x-api-key": self.key(), "anthropic-version": "2023-06-01"},
                         timeout=int(self.cfg.get("timeout", 300)))
        text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input") or {}}
                 for b in data.get("content") or [] if b.get("type") == "tool_use"]
        return Reply(text.strip(), calls)
