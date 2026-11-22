"""The librarian's models (talker and writer) through Goblin Launcher's shared providers.

A model entry in librarian.yaml names either a shared provider or a full endpoint:
  models:
    qwen:   {provider: local, model: "qwen3.8:27b"}                  # a provider from config.yaml
    writer: {provider: openai, base_url: http://host:11434/v1, model: x, key_ref: writer}
    test:   {provider: mock, script: path/to/script.json}            # scripted, for tests

Internal message format (unchanged): assistant tool calls are {"id", "name", "args"}.
"""
from __future__ import annotations

import json
import uuid

from ..providers import ProviderError, make as make_shared


class ChatResult:
    def __init__(self, text="", tool_calls=None, tokens_in=0, tokens_out=0):
        self.text = text or ""
        self.tool_calls = tool_calls or []
        self.tokens_in, self.tokens_out = tokens_in, tokens_out


def _to_shared(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "assistant":
            out.append({"role": "assistant", "content": m.get("content") or "",
                        "tool_calls": [{"id": c["id"], "name": c["name"], "arguments": c.get("args") or {}}
                                       for c in m.get("tool_calls") or []]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "name": m.get("name", ""),
                        "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


class Shared:
    def __init__(self, provider, mcfg: dict):
        self.p, self.mcfg = provider, mcfg

    def chat(self, system, messages, tools):
        specs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["schema"]}} for t in tools or []]
        r = self.p.chat(self.mcfg["model"], _to_shared(system, messages), specs or None,
                        int(self.mcfg.get("max_tokens", 4096)), float(self.mcfg.get("temperature", 0.2)))
        return ChatResult(r.content, [{"id": c["id"] or uuid.uuid4().hex[:12], "name": c["name"],
                                       "args": c.get("arguments") or {}} for c in r.tool_calls])


class MockProvider:
    """Replays [{"text": ..., "tool_calls": [{"name", "args"}]}]. One position per script file,
    so a talker and a writer can use separate files."""
    _pos: dict[str, int] = {}

    def __init__(self, mcfg: dict):
        self.path = mcfg["script"]
        with open(self.path) as f:
            self.script = json.load(f)

    def chat(self, system, messages, tools):
        i = MockProvider._pos.get(self.path, 0)
        if i >= len(self.script):
            return ChatResult("(mock script exhausted)")
        MockProvider._pos[self.path] = i + 1
        step = self.script[i]
        calls = [{"id": uuid.uuid4().hex[:12], "name": c["name"], "args": c.get("args", {})}
                 for c in step.get("tool_calls", [])]
        return ChatResult(step.get("text", ""), calls, step.get("tokens_in", 100), step.get("tokens_out", 20))


def make(cfg, model_name):
    mcfg = cfg.model(model_name)
    kind = mcfg.get("provider", "openai")
    if kind == "mock":
        return MockProvider(mcfg)
    shared = (cfg.goblin.get("providers") or {}) if getattr(cfg, "goblin", None) else {}
    if kind in shared:
        return Shared(make_shared(kind, shared[kind]), mcfg)
    if kind in ("openai", "anthropic", "ollama"):     # a full endpoint in the model entry itself
        pcfg = {"type": kind, "base_url": mcfg.get("base_url")}
        p = make_shared(model_name, pcfg)
        key = cfg.resolve_key(mcfg)
        p.key = lambda: key
        return Shared(p, mcfg)
    raise ProviderError(f"model {model_name!r}: unknown provider {kind!r}")


__all__ = ["ChatResult", "ProviderError", "make"]
