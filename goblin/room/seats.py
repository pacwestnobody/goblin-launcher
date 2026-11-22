"""Room seats come from config `seats:`. Each names a provider (any entry in `providers:`) or a
command-line agent (`grok-cli`, `claude-code`).

  seats:
    qwen:  {label: "Qwen (local)", provider: local, model: "qwen3:8b", color: "#3ddc97", guard: true,
            identity: "You are Qwen ...", aliases: [qwen]}
    grok:  {label: "Grok Build", provider: grok-cli, model: grok-4.7, aliases: [grok, grok build]}
"""
from __future__ import annotations

from ..agents.cli import bin_of
from ..providers import ProviderError, make

CLI = {"grok-cli": "grok", "claude-code": "claude"}


def all_seats(cfg) -> dict[str, dict]:
    out = {}
    for sid, s in (cfg.get("seats") or {}).items():
        s = {"id": sid, "label": sid, "color": "#888888", "tools": True, **(s or {})}
        out[sid] = s
    return out


def is_local(cfg, seat: dict) -> bool:
    return ((cfg.get("providers") or {}).get(seat.get("provider")) or {}).get("type") == "ollama"


def ready(cfg, seat: dict) -> tuple[bool, str]:
    prov = seat.get("provider")
    if prov in CLI:
        b = bin_of(cfg, CLI[prov])
        return (True, "") if b.exists() else (False, f"{b} not installed")
    pc = (cfg.get("providers") or {}).get(prov)
    if not pc:
        return False, f"no provider {prov!r} in config"
    try:
        return make(prov, pc).ready()
    except ProviderError as e:
        return False, str(e)


def public(cfg) -> list[dict]:
    rows = []
    for s in all_seats(cfg).values():
        ok, why = ready(cfg, s)
        rows.append({"id": s["id"], "label": s["label"], "color": s["color"], "provider": s.get("provider"),
                     "model": s.get("model"), "local": is_local(cfg, s), "ready": ok, "why": why})
    return rows


def addressed(text: str, seat_ids: list[str], seats: dict) -> list[str]:
    """Seats the message names, in the order named. Longer names win ("qwen coder" over "qwen")."""
    low = text.lower()
    hits: list[tuple[int, int, str]] = []
    for sid in seat_ids:
        s = seats.get(sid) or {}
        names = {sid.lower(), str(s.get("label") or "").lower(), *[a.lower() for a in s.get("aliases") or []]}
        for n in filter(None, names):
            i = low.find(n)
            if i >= 0:
                hits.append((i, -len(n), sid))
    hits.sort()
    taken: list[tuple[int, int]] = []
    order: list[str] = []
    for i, neg, sid in hits:
        span = (i, i - neg)
        if any(a <= span[0] < b for a, b in taken):      # inside a longer name already matched
            continue
        taken.append(span)
        if sid not in order:
            order.append(sid)
    return order
