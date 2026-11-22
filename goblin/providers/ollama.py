"""Ollama's native /api/chat (tools supported). No key.

Never sends num_ctx: a per-request context smaller than the Modelfile's reloads a short slot,
and a tool loop then fills it and condenses every turn (seen 2026-07-20).
"""
from __future__ import annotations

import json
import urllib.request

from .base import Provider, Reply, parse_args, post_json


def base_of(url: str) -> str:
    url = (url or "http://127.0.0.1:11434").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def to_ollama(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "content": m.get("content") or "", "name": m.get("name") or "",
                        "tool_call_id": m.get("tool_call_id") or ""})
        elif role == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": [
                {"id": tc["id"], "function": {"name": tc["name"], "arguments": tc.get("arguments") or {}}}
                for tc in m["tool_calls"]]})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


class Ollama(Provider):
    default_base = "http://127.0.0.1:11434"
    needs_key = False

    def ready(self):
        try:
            with urllib.request.urlopen(base_of(self.base_url) + "/api/version", timeout=2):
                return True, ""
        except OSError:
            if self.cfg.get("start_command"):
                return True, "will start on demand"
            return False, f"Ollama not running at {self.base_url}"

    def chat(self, model, messages, tools=None, max_tokens=2048, temperature=0.4):
        payload = {"model": model, "messages": to_ollama(messages), "stream": False,
                   "options": {"num_predict": max_tokens, "temperature": temperature}}
        if tools:
            payload["tools"] = tools          # don't send think:false with tools (Ollama 400s)
        data = post_json(base_of(self.base_url) + "/api/chat", payload, {}, timeout=int(self.cfg.get("timeout", 900)))
        msg = data.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append({"id": tc.get("id") or f"call_{i}_{fn.get('name')}", "name": fn.get("name"),
                          "arguments": parse_args(fn.get("arguments"))})
        return Reply((msg.get("content") or "").strip(), calls)

    # ---- model residency (used by goblin.gpu) -----------------------------------------
    def loaded(self) -> list[str]:
        with urllib.request.urlopen(base_of(self.base_url) + "/api/ps", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]

    def set_resident(self, model: str, keep_alive) -> None:
        post_json(base_of(self.base_url) + "/api/generate", {"model": model, "keep_alive": keep_alive}, {},
                  timeout=600)
