"""Reading a terminal session's trace the way a Ledger job records itself.

Sessions from Grok Build or Claude Code are traced call by call, but they carry no job kind, no place key,
no abandoned fork and no finish record, so the writer could never use them. This module supplies those,
in plain code, from the trace alone (never from anything a model said):

1. Shell commands that match an allowlisted read exactly are marked as that read (read_id, read_args).
2. A *check* is such a read that once failed and later passed with the same arguments.
3. The job kind follows from the check's read (systemctl → service_start_fix, py_import → python_dep_fix,
   ollama_tags → ollama_fix, ping/dns/http → network_diag).
4. The dead end: between the first failure and the pass, the check was run again after some attempt and
   still failed. That still-failing check is where the path was abandoned (the fork's at_step).
5. The finish: success, with the passing check as evidence.

A session where any of this is missing gets no kind and writes nothing. That keeps cards trustworthy.
"""
from __future__ import annotations

import json
import re
import shlex

from .keys import make_key

SHELL_TOOLS = {"Bash", "bash", "run_terminal_command", "shell", "run_command", "exec", "terminal"}
KIND_OF_READ = {
    "systemctl_status": "service_start_fix",
    "systemctl_user_status": "service_start_fix",
    "py_import": "python_dep_fix",
    "pip_show": "python_dep_fix",
    "ollama_tags": "ollama_fix",
    "ping_once": "network_diag",
    "dns_query": "network_diag",
    "https_page": "network_diag",
    "http_get": "network_diag",
    "ip_route_default": "network_diag",
}


def command_of(rec) -> str | None:
    """The shell command of a recorded call, if it was one."""
    if rec.get("kind") != "call" or rec.get("tool") not in SHELL_TOOLS:
        return None
    args = rec.get("args") or {}
    if isinstance(args, dict) and "redacted" in args:
        args = args["redacted"]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    cmd = args.get("command") or args.get("cmd")
    return cmd if isinstance(cmd, str) else None


def _words(cmd: str) -> list[str] | None:
    """The first command of a pipeline, without redirections: what actually produced the exit code shown."""
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    out = []
    for p in parts:
        if p in ("|", "&&", ";", "||") or p.startswith("|"):
            break
        if p in ("2>&1", "2>/dev/null", ">/dev/null", "&>/dev/null") or p.startswith("2>") or p.startswith(">"):
            continue
        out.append(p)
    return out or None


def _split_placeholders(token: str):
    """'https://{0}/' → [('https://', None), ('', 0), ('/', None)]"""
    out = []
    pos = 0
    for m in re.finditer(r"\{(\d+)\}", token):
        if m.start() > pos:
            out.append((token[pos:m.start()], None))
        out.append(("", int(m.group(1))))
        pos = m.end()
    if pos < len(token):
        out.append((token[pos:], None))
    return out


def match_read(cmd: str, reads: dict) -> tuple[str, list[str]] | None:
    """(read_id, args) if the command is exactly one of the allowlisted reads run on this machine."""
    words = _words(cmd)
    if not words:
        return None
    for rid, spec in reads.items():
        if spec.get("trivial") or spec.get("target", "local") not in ("local", "job"):
            continue
        tmpl = spec.get("cmd") or []
        if len(tmpl) != len(words):
            continue
        got: dict[int, str] = {}
        ok = True
        for t, w in zip(tmpl, words):
            if "{" not in t:
                if t != w:
                    ok = False
                    break
                continue
            # a word with placeholders in it, e.g. "https://{0}/" or "{0}/status"
            pattern = "".join(f"(?P<p{m}>.+?)" if m is not None else re.escape(lit)
                              for lit, m in _split_placeholders(t))
            hit = re.fullmatch(pattern, w)
            if not hit:
                ok = False
                break
            for name, val in hit.groupdict().items():
                got[int(name[1:])] = val
        if ok:
            return rid, [got[i] for i in sorted(got)]
    return None


def _failed(rec) -> bool:
    return rec.get("exit") not in (0, None)


def interpret(records: list[dict], cfg) -> dict | None:
    """
    Work out kind, key, fork and finish for a terminal session. Returns
    {kind, key, artifact, family, records, check, fork_step, evidence_step, reason} or None.
    `records` are the trace's records (calls in order); the returned records are a new list with read ids
    marked and the fork and finish records put in.
    """
    reads = cfg["reads"]
    kinds = cfg["job_kinds"]
    calls = [dict(r) for r in records if r.get("kind") == "call"]
    for c in calls:
        cmd = command_of(c)
        hit = match_read(cmd, reads) if cmd else None
        if hit:
            c["read_id"], c["read_args"] = hit
    # every check signature: the same read with the same arguments, in order
    sigs: dict[tuple, list[dict]] = {}
    for c in calls:
        if c.get("read_id") in KIND_OF_READ:
            sigs.setdefault((c["read_id"], tuple(c["read_args"])), []).append(c)
    best = None
    for (rid, args), runs in sigs.items():
        kind = KIND_OF_READ[rid]
        if kind not in kinds or not kinds[kind].get("stable"):
            continue
        first_fail = next((r for r in runs if _failed(r)), None)
        if not first_fail:
            continue
        passed = next((r for r in runs if r["step"] > first_fail["step"] and not _failed(r)), None)
        if not passed:
            continue
        # a still-failing rerun of the check after some other command was tried: the abandoned path
        killer = None
        for r in runs:
            if first_fail["step"] < r["step"] < passed["step"] and _failed(r):
                tried = [c for c in calls if first_fail["step"] < c["step"] < r["step"] and not c.get("read_id")]
                if tried:
                    killer = r
        if not killer:
            continue
        span = passed["step"] - first_fail["step"]
        if best is None or span > best["span"]:
            best = {"rid": rid, "args": list(args), "kind": kind, "first": first_fail, "killer": killer, "passed": passed, "span": span}
    if not best:
        return None
    family = kinds[best["kind"]].get("tool_family", "shell")
    artifact = best["args"][0] if best["args"] else best["rid"]
    key = make_key(best["kind"], family, artifact, best["first"].get("output") or "")
    out = []
    by_step = {c["step"]: c for c in calls}
    for r in records:
        if r.get("kind") == "call":
            out.append(by_step.get(r["step"], r))
            if r["step"] == best["killer"]["step"]:
                out.append({"kind": "fork", "step": r["step"], "at_step": r["step"],
                            "reason": f"{best['rid']} still failing after the attempt before it"})
        elif r.get("kind") not in ("fork", "finish"):
            out.append(r)
    out.append({"kind": "finish", "step": best["passed"]["step"], "status": "success", "evidence_step": best["passed"]["step"]})
    return {
        "kind": best["kind"], "key": key, "artifact": artifact, "family": family, "records": out,
        "check": [best["rid"], best["args"]], "fork_step": best["killer"]["step"], "evidence_step": best["passed"]["step"],
        "reason": f"{best['rid']} {' '.join(best['args'])} failed at step {best['first']['step']}, still failed at "
                  f"{best['killer']['step']} after an attempt, passed at {best['passed']['step']}",
    }
