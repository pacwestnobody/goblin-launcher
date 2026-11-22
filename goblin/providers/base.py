"""The provider interface and shared HTTP plumbing (stdlib only)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


class ProviderError(RuntimeError):
    pass


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # [{"id", "name", "arguments": dict}]


class Provider:
    """Subclasses implement chat(). Messages and tools are OpenAI-style."""

    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg
        self.base_url = str(cfg.get("base_url") or self.default_base or "").rstrip("/")

    default_base = ""
    needs_key = True

    def key(self) -> str:
        if self.cfg.get("key_env") and os.environ.get(self.cfg["key_env"]):
            return os.environ[self.cfg["key_env"]].strip()
        if self.cfg.get("key_file"):
            try:
                return Path(self.cfg["key_file"]).expanduser().read_text().strip()
            except OSError:
                return ""
        return ""

    def ready(self) -> tuple[bool, str]:
        if self.needs_key and not self.key():
            where = self.cfg.get("key_env") or self.cfg.get("key_file") or "key_env / key_file"
            return False, f"no API key ({where})"
        return True, ""

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int = 2048, temperature: float = 0.4) -> Reply:
        raise NotImplementedError


def post_json(url: str, payload: dict, headers: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:800]}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"can't reach {url}: {e.reason}") from None


def parse_args(raw) -> dict:
    """Tool-call arguments arrive as a dict or a JSON string."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        return {"_raw": str(raw)}
