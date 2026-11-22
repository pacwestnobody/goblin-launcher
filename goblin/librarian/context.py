"""Per-turn context: what the librarian puts in front of any agent, by code, every turn.

    assemble(service, prompt, ...) -> {"text": str, "parts": {...}}

Three sources, each with its own share of a character budget (≈4 chars per token):
  shelf      verified map facts. Probes are slow (seconds), so they come from the service's
             shelf cache, refreshed in the background; only facts that passed are used.
             Only facts that share a word with the prompt are used.
  knowledge  notes-index retrieval, only past the auto-injection gate, and only hits
             that share a word with the prompt or score high on their own.
  card       a stored route for this exact failure, only when the job is keyed and the
             card's applies-probe passes (live mode; see injector).
Nothing is added when nothing is relevant: an empty string is a valid answer.
"""
import re
import time

from . import injector, knowledge
from .keys import make_key

DEFAULT_BUDGET = {"total": 7000, "shelf": 1800, "knowledge": 4200, "card": 1200}
_WORD = re.compile(r"[a-z0-9][a-z0-9._:-]{2,}")
_STOP = {"the", "and", "for", "with", "this", "that", "what", "which", "are", "from", "how", "does",
         "can", "you", "your", "its", "not", "but", "has", "have", "was", "were", "will", "into",
         "then", "than", "there", "here", "about", "only", "never", "runs", "run", "on"}


def _words(text):
    """Lowercase words, plus their pieces split on . / _ - : (so truenas.md also yields truenas)."""
    out = set()
    for w in _WORD.findall((text or "").lower()):
        w = w.strip(".:,-")
        out.add(w)
        # pieces of a.b/c names count; pieces of numbers (192 of 192.168.1.20) don't
        out.update(p for p in re.split(r"[./_:-]+", w) if len(p) >= 3 and not p.isdigit())
    return out - _STOP


# Automatic injection needs a higher bar than an explicit lookup. Measured 2026-11-22 on the owner's
# eval questions (all >= 0.571) vs unrelated prompts (<= 0.508, zero shared words).
DEFAULT_GATE = {"min_best": 0.55, "keep_score": 0.62}


def auto_gate(cfg):
    return {**DEFAULT_GATE, **((cfg.get("context") or {}).get("auto_gate") or {})}


def budget(cfg):
    return {**DEFAULT_BUDGET, **((cfg.get("context") or {}).get("budget") or {})}


def shelf_block(passing_facts, prompt, limit, owner="the owner"):
    """passing_facts: [{"id", "fact"}]. Relevant facts first, then the rest while they fit."""
    if not passing_facts:
        return "", []
    pw = _words(prompt)
    scored = []
    for i, f in enumerate(passing_facts):
        fw = _words(f["fact"]) | _words(f["id"].replace("-", " "))
        scored.append((-len(pw & fw), i, f))
    scored.sort()
    lines, used, ids = [], 0, []
    for neg_overlap, _, f in scored:
        if neg_overlap == 0:
            break                      # only facts that share a word with the prompt
        line = f"- {f['fact']}"
        if used + len(line) + 1 > limit:
            continue
        lines.append(line)
        ids.append(f["id"])
        used += len(line) + 1
    if not lines:
        return "", []
    return (f"<verified_map>\nFacts about {owner}'s setup whose probes passed recently:\n"
            + "\n".join(lines) + "\n</verified_map>"), ids


def assemble(service, prompt, *, agent=None, kind=None, artifact=None, error=None,
             tool_family=None, use_knowledge=True):
    cfg = service.cfg
    b = budget(cfg)
    t0 = time.time()
    parts, blocks, notes = {}, [], []

    owner = (getattr(cfg, "goblin", None) or {}).get("owner", "the owner")
    shelf_text, shelf_ids = shelf_block(service.shelf_passing(), prompt, b["shelf"], owner)
    if shelf_text:
        blocks.append(shelf_text)
    parts["shelf"] = {"ids": shelf_ids, "chars": len(shelf_text),
                      "checked": service.shelf_checked_at()}

    k_text = ""
    if use_knowledge and (prompt or "").strip():
        try:
            res = knowledge.search(cfg, prompt)
            gate = auto_gate(cfg)
            if res["best"] >= gate["min_best"]:
                pw = _words(prompt)
                res = {**res, "hits": [h for h in res["hits"]
                                       if h["score"] >= gate["keep_score"]
                                       or pw & _words(h["source"] + " " + h["text"])]}
                k_text = knowledge.render(res, max_chars=b["knowledge"], owner=owner)
            parts["knowledge"] = {"sources": [h["source"] for h in res["hits"]], "best": res["best"],
                                  "used": bool(k_text)}
        except knowledge.KnowledgeError as e:
            parts["knowledge"] = {"error": str(e)}
            notes.append("knowledge library unavailable this turn")
    if k_text:
        blocks.append(k_text)

    card_text, card_id = "", None
    if kind and artifact and error:
        try:
            fam = tool_family or (cfg["job_kinds"].get(kind) or {}).get("tool_family", "shell")
            key = make_key(kind, fam, artifact, error)
        except ValueError:
            key = None
        if key and injector.should_consult(cfg, service.store, kind):
            row, block = injector.consult(cfg, service.store, key)
            if row and len(block) <= b["card"]:
                card_text, card_id = block, row["id"]
        parts["card"] = {"key": key, "card_id": card_id}
    if card_text:
        blocks.append(card_text)

    text = "\n\n".join(blocks)
    if len(text) > b["total"]:
        text = text[: b["total"]].rsplit("\n", 1)[0] + "\n[librarian: context trimmed to budget]"
    parts["ms"] = int((time.time() - t0) * 1000)
    parts["chars"] = len(text)
    if notes:
        parts["notes"] = notes
    return {"text": text, "parts": parts, "agent": agent}
