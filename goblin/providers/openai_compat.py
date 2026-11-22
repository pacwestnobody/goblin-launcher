"""Any OpenAI-compatible /chat/completions API: OpenAI, OpenRouter, Groq, Hugging Face router,
LM Studio, vLLM, llama.cpp server, Ollama's /v1, ... Set base_url and a key."""
from __future__ import annotations

import json

from .base import Provider, Reply, parse_args, post_json


def to_openai(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "tool", "content": m.get("content") or ""})
        elif role == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {})}}
                for tc in m["tool_calls"]]})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class OpenAICompat(Provider):
    default_base = "https://api.openai.com/v1"

    def headers(self, model: str) -> dict:
        h = {"Authorization": f"Bearer {self.key()}"} if self.key() else {}
        return {**h, **(self.cfg.get("headers") or {})}

    def chat(self, model, messages, tools=None, max_tokens=2048, temperature=0.4):
        payload = {"model": model, "messages": to_openai(messages), "max_tokens": max_tokens,
                   "temperature": temperature}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        data = post_json(self.base_url + "/chat/completions", payload, self.headers(model),
                         timeout=int(self.cfg.get("timeout", 300)))
        msg = ((data.get("choices") or [{}])[0]).get("message") or {}
        calls = [{"id": tc.get("id") or f"call_{i}", "name": (tc.get("function") or {}).get("name"),
                  "arguments": parse_args((tc.get("function") or {}).get("arguments"))}
                 for i, tc in enumerate(msg.get("tool_calls") or [])]
        return Reply((msg.get("content") or "").strip(), calls)
