"""The writer. Runs after the job, from the tool trace only.

It never sees the talker's prose and never reads the store it is about to
change (the one exception is the existing card's own probe, run by code to
decide hit-vs-supersede). A model proposes the card; the checker decides.
"""
import hashlib
import json
import re
import time

from . import checker, probes, providers
from .store import Store

WRITER_SYSTEM = """You turn a tool trace into one route card, as JSON only.
You see tool calls and their outputs. You do not see any conversation.

Return exactly one JSON object:
{
  "type": "repair",
  "route": ["short imperative step", ...],          // shortest ordered steps that reached the checked result
  "dead_fork": {"path": "the tempting wrong path", "killed_by_step": <step number of the killing observation>,
                "observation": "what that step showed"},
  "probes": {
    "applies": {"read": "<read id>", "args": [...], "match": {...}},   // true in the broken state, before the fix
    "success": {"read": "<read id>", "args": [...], "match": {...}},   // FALSE in the dead-fork state, TRUE after success
    "dead_fork": {"read": "<read id>", "args": [...], "match": {...}}  // optional: reproduces the killing observation
  }
}
Rules:
- Probes may only use read ids from the allowlist, with args exactly as they appear in the trace.
- The success probe must use a read that appears in the trace both before the abandon_fork and after it
  (a later call or a rerun record), and its match must tell those two outputs apart.
- match keys: exit, regex, not_regex, json {path, equals}, hash. Prefer a regex on a specific field.
- Facts and steps only. No advice, no instructions to a model, no secrets.
If the trace cannot support such a card, return {"type": "none", "reason": "..."}."""


def gate(trace, cfg):
    m = trace.meta
    kinds = cfg["job_kinds"]
    if m.get("kind") not in kinds:
        return "job kind not in config job_kinds"
    if not kinds[m["kind"]].get("stable", False):
        return "job kind is not marked stable"
    if not m.get("key"):
        return "no place key (no error fingerprint)"
    fin = [r for r in trace.records if r["kind"] == "finish"]
    if not fin or fin[-1]["status"] != "success":
        return "job did not succeed"
    ev = trace.by_step(fin[-1].get("evidence_step"))
    if ev is None or ev["exit"] != 0:
        return "no passing check at evidence_step"
    if len(trace.calls()) < cfg["write"]["min_steps"]:
        return f"short walk ({len(trace.calls())} calls < {cfg['write']['min_steps']})"
    if not any(r["kind"] == "fork" for r in trace.records):
        return "no abandoned fork: the tree was not wide"
    return None


def _trace_for_writer(trace):
    out = []
    for r in trace.records:
        if r["kind"] == "call":
            out.append({"step": r["step"], "tool": r["tool"], "read_id": r.get("read_id"),
                        "args": r.get("read_args") if r.get("read_id") else r.get("args"),
                        "exit": r["exit"], "output": checker.redact(r["output"][:1500])})
        elif r["kind"] == "fork":
            out.append({"step": r["step"], "abandon_fork": {"at_step": r["at_step"],
                                                           "reason": checker.redact(r["reason"])}})
        elif r["kind"] == "rerun":
            out.append({"rerun_after_success": True, "of_step": r["of_step"], "read_id": r["read_id"],
                        "args": r["read_args"], "exit": r["exit"],
                        "output": checker.redact(r["output"][:1500])})
        elif r["kind"] == "finish":
            out.append({"step": r["step"], "finish": r["status"], "evidence_step": r["evidence_step"]})
    return out


def _parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            try:
                return json.loads(text[start:i + 1])
            except json.JSONDecodeError:
                return None
    return None


def propose(trace, cfg):
    name, _ = cfg.role_model("writer")
    prov = providers.make(cfg, name)
    allow = {rid: s.get("description", "") for rid, s in cfg["reads"].items() if not s.get("trivial")}
    user = json.dumps({"read_allowlist": allow, "trace": _trace_for_writer(trace)})
    for _ in range(2):
        res = prov.chat(WRITER_SYSTEM, [{"role": "user", "content": user}], [])
        card = _parse_json(res.text)
        if card is not None:
            return card
    return None


def route_hash(card):
    return hashlib.sha256(json.dumps(card["route"]).encode()).hexdigest()[:16]


def write_job(trace, cfg, store: Store):
    key = trace.meta.get("key")
    jid = trace.job_id
    why = gate(trace, cfg)
    if why:
        store.log_write(jid, key, "no_card", why)
        return {"outcome": "no_card", "reason": why}

    existing = store.readable(key)
    if existing:
        outcome, _, _ = probes.run_probe(cfg, existing["card"]["probes"]["success"])
        if outcome == probes.PASS:
            store.hit(existing["id"])
            store.log_write(jid, key, "hit_existing", f"card {existing['id']}")
            return {"outcome": "hit_existing", "card_id": existing["id"]}

    card = propose(trace, cfg)
    if not card or card.get("type") == "none":
        reason = (card or {}).get("reason", "writer returned nothing usable")
        store.log_write(jid, key, "no_card", f"writer: {reason}")
        return {"outcome": "no_card", "reason": f"writer: {reason}"}

    ok, reason, clean = checker.check_card(card, trace, cfg)
    if not ok:
        store.log_write(jid, key, "rejected", reason)
        return {"outcome": "rejected", "reason": reason}

    clean["created"] = time.strftime("%Y-%m-%d")
    clean["key"] = key
    store.log_key(jid, key, trace.meta.get("kind"), route_hash(clean))
    if cfg["mode"] == "shadow":
        store.log_write(jid, key, "shadow_accepted", "")
        return {"outcome": "shadow_accepted", "card": clean}
    cid = store.put(key, clean, jid)
    store.log_write(jid, key, "written", f"card {cid}")
    store.purge(cfg["store"]["cap_bytes"], cfg["store"]["superseded_delay_s"])
    return {"outcome": "written", "card_id": cid, "card": clean}
