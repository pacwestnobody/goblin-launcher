"""xAI's Grok CLI chat proxy, signed in with `grok login` (no API key needed).

The proxy wants the CLI's identity headers and refuses clients older than 1.0.13 (HTTP 426), so
the installed `grok --version` is sent on every call.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .openai_compat import OpenAICompat

GROK_HOME = Path.home() / ".grok"


def grok_token() -> str:
    try:
        data = json.loads((GROK_HOME / "auth.json").read_text())
    except (OSError, ValueError):
        return ""
    for v in data.values():
        if isinstance(v, dict) and v.get("key"):
            return str(v["key"])
    return ""


_version_cache: dict[str, str] = {}


def grok_version(binary: Path) -> str:
    if "v" not in _version_cache:
        try:
            out = subprocess.check_output([str(binary), "--version"], text=True, timeout=8, stderr=subprocess.STDOUT)
            m = re.search(r"(\d+\.\d+\.\d+)", out)
            _version_cache["v"] = m.group(1) if m else "1.0.13"
        except (OSError, subprocess.SubprocessError):
            _version_cache["v"] = "1.0.13"
    return _version_cache["v"]


class GrokProxy(OpenAICompat):
    default_base = "https://cli-chat-proxy.grok.com/v1"
    needs_key = False

    def ready(self):
        return (True, "") if grok_token() else (False, "not signed in: run `grok login`")

    def headers(self, model):
        binary = Path(self.cfg.get("grok_bin") or GROK_HOME / "bin" / "grok").expanduser()
        return {"Authorization": f"Bearer {grok_token()}", "User-Agent": "xai-grok-cli",
                "X-XAI-Token-Auth": "xai-grok-cli", "x-grok-client-version": grok_version(binary),
                "x-grok-model-override": model}
