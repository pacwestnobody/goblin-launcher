"""Model providers behind one interface.

A provider is configured by `type` (config.yaml -> providers):

  providers:
    local:      {type: ollama, base_url: http://127.0.0.1:11434}
    openai:     {type: openai, base_url: https://api.openai.com/v1, key_env: OPENAI_API_KEY}
    openrouter: {type: openai, base_url: https://openrouter.ai/api/v1, key_env: OPENROUTER_API_KEY}
    anthropic:  {type: anthropic, key_env: ANTHROPIC_API_KEY}
    grok-proxy: {type: grok_proxy}            # xAI's CLI proxy, signed in with `grok login`

Keys come from an environment variable (key_env) or a mode-600 file (key_file), never from
config.yaml itself. Every provider takes OpenAI-style messages and tool specs and returns a Reply;
conversion to each wire format happens inside the provider.

Adding a provider = one module with a class that has chat(), registered in TYPES.
"""
from __future__ import annotations

from .base import Provider, ProviderError, Reply
from .anthropic import Anthropic
from .grok_proxy import GrokProxy
from .ollama import Ollama
from .openai_compat import OpenAICompat

TYPES: dict[str, type[Provider]] = {
    "openai": OpenAICompat,
    "anthropic": Anthropic,
    "ollama": Ollama,
    "grok_proxy": GrokProxy,
}


def make(name: str, cfg: dict) -> Provider:
    kind = (cfg or {}).get("type") or name
    if kind not in TYPES:
        raise ProviderError(f"provider {name!r}: unknown type {kind!r} (known: {', '.join(TYPES)})")
    return TYPES[kind](name, cfg or {})


__all__ = ["Provider", "ProviderError", "Reply", "TYPES", "make"]
