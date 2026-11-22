"""The checker. Pure code. It did not produce the trace and it is not a model.

It accepts or rejects a proposed card against recorded evidence only.
There is no live replay at accept time.
"""
import json
import re

from . import probes

# Lines that read like instructions to a model are stripped at write time.
_INSTRUCTION_RX = re.compile(
    r"(ignore (all |any |the )?(previous|prior|above)|disregard|system prompt|you are (now|an?)\b|"
    r"\byou must\b|\bdo not tell\b|\bpretend\b|\bact as\b|<\s*/?\s*(system|instructions?)\s*>|"
    r"\bnew instructions\b|\boverride\b)", re.I)
_SECRET_RX = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(pass(word)?|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+|"
    r"Bearer\s+[A-Za-z0-9._-]{12,})", re.I)
REDACTED = "[REDACTED]"


def redact(text):
    return _SECRET_RX.sub(REDACTED, text or "")


def strip_instructions(text):
    kept = [s for s in re.split(r"(?<=[.;!?])\s+|\n", text or "") if s and not _INSTRUCTION_RX.search(s)]
    return " ".join(kept).strip()


def _records_for(trace, read_id, read_args):
    out = []
    for r in trace.records:
        if r["kind"] in ("call", "rerun") and r.get("read_id") == read_id and r.get("read_args") == read_args:
            out.append(r)
    return out


def _order(trace, r):
    """Position of a record in the trace (reruns come after everything)."""
    return trace.records.index(r)


def check_card(card, trace, cfg):
    """Return (ok, reason, cleaned_card)."""
    w = cfg["write"]
    cap = w["max_field_chars"]
    if not isinstance(card, dict) or card.get("type") != "repair":
        return False, "card must be an object with type 'repair'", None

    route = card.get("route")
    if not isinstance(route, list) or not (1 <= len(route) <= w["max_route_steps"]):
        return False, f"route must be a list of 1..{w['max_route_steps']} steps", None
    df = card.get("dead_fork")
    if not isinstance(df, dict) or not isinstance(df.get("killed_by_step"), int):
        return False, "dead_fork needs path, killed_by_step (int), observation", None

    clean_route = []
    for s in route:
        if not isinstance(s, str):
            return False, "route steps must be strings", None
        s = strip_instructions(s)
        if not s:
            return False, "a route step was nothing but instruction-like text", None
        if len(s) > cap:
            return False, f"route step over {cap} chars", None
        clean_route.append(s)
    path_txt = strip_instructions(str(df.get("path", "")))
    obs_txt = strip_instructions(str(df.get("observation", "")))
    if not path_txt or not obs_txt or len(path_txt) > cap or len(obs_txt) > cap:
        return False, "dead_fork path/observation missing, instruction-like, or too long", None

    blob = json.dumps([clean_route, path_txt, obs_txt])
    if REDACTED in blob or _SECRET_RX.search(blob):
        return False, "card contains redacted or secret material; route does not survive redaction", None

    pr = card.get("probes") or {}
    for name in ("applies", "success"):
        if name not in pr:
            return False, f"missing probes.{name}", None
        err = probes.validate_probe(cfg, pr[name])
        if err:
            return False, f"probes.{name}: {err}", None
    if "dead_fork" in pr:
        err = probes.validate_probe(cfg, pr["dead_fork"])
        if err:
            return False, f"probes.dead_fork: {err}", None

    # ---- evidence: the fork, the two recorded observations ----------------
    forks = [r for r in trace.records if r["kind"] == "fork" and r["at_step"] == df["killed_by_step"]]
    if not forks:
        return False, "no abandon_fork in the trace points at killed_by_step", None
    fork = forks[-1]
    fork_pos = _order(trace, fork)
    fin = [r for r in trace.records if r["kind"] == "finish"]
    if not fin or fin[-1]["status"] != "success":
        return False, "job did not finish with success", None

    sp = pr["success"]
    recs = _records_for(trace, sp["read"], sp["args"])
    before = [r for r in recs if _order(trace, r) < fork_pos]
    after = [r for r in recs if _order(trace, r) > fork_pos]
    if not before or not after:
        return False, ("success probe read was not recorded in both states "
                       "(dead-fork state and after success)"), None
    pre, post = before[-1], after[-1]
    if not probes.eval_match(sp["match"], post["exit"], post["output"]):
        return False, "success probe does not pass on the after-success record", None
    if probes.eval_match(sp["match"], pre["exit"], pre["output"]):
        return False, "success probe passes on the dead-fork record too: it measures nothing", None

    ap = pr["applies"]
    a_before = [r for r in _records_for(trace, ap["read"], ap["args"]) if _order(trace, r) < fork_pos]
    if not a_before or not any(probes.eval_match(ap["match"], r["exit"], r["output"]) for r in a_before):
        return False, "applies probe has no passing record in the pre-fix state", None

    if "dead_fork" in pr:
        dp = pr["dead_fork"]
        d_before = [r for r in _records_for(trace, dp["read"], dp["args"]) if _order(trace, r) <= fork_pos]
        if not any(probes.eval_match(dp["match"], r["exit"], r["output"]) for r in d_before):
            return False, "dead_fork probe does not reproduce the killing observation", None

    cleaned = {
        "type": "repair",
        "route": clean_route,
        "dead_fork": {"path": path_txt, "killed_by_step": df["killed_by_step"], "observation": obs_txt},
        "probes": {k: {"read": v["read"], "args": v["args"], "match": v["match"]} for k, v in pr.items()
                   if k in ("applies", "success", "dead_fork")},
    }
    # What the talker would read (route + fork) must cost less than the walk it replaces.
    shown_bytes = len(" ".join(clean_route + [path_txt, obs_txt]).encode())
    walk_bytes = sum(len(json.dumps(r.get("args", {}))) + len(r.get("output_norm", "")) for r in trace.calls())
    if shown_bytes >= walk_bytes:
        return False, f"card text ({shown_bytes}B) is not shorter than the walk ({walk_bytes}B)", None
    return True, "ok", cleaned
