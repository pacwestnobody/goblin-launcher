"""Declarative probes. A card never carries code.

An allowlisted read lives in config.yaml:
  reads:
    pip_show:
      description: "Show an installed Python package"
      cmd: ["pip", "show", "{0}"]       # {0}, {1}... are the only substitutions
      target: sandbox
      timeout: 15
      arg_pattern: "^[A-Za-z0-9._:/@=+-]{1,200}$"
      exit_meaningful: false             # true if exit status alone says something real
      trivial: false                     # true for file-exists / is-alive style reads

A probe is {read, args, match}. A match is any of:
  {"exit": 0}
  {"regex": "Version: 2\\.31"}            searched in raw output
  {"not_regex": "ImportError"}
  {"json": {"path": "a.b.0", "equals": "x"}}
  {"hash": "<sha256 of normalized output>"}
Every condition given must hold.
"""
import json
import re

from . import targets
from .keys import normalize, output_hash

PASS, FAIL, COULDNT = "pass", "fail", "couldnt_run"
DEFAULT_ARG_PATTERN = r"^[A-Za-z0-9._:/@=+,-]{1,200}$"
MATCH_KEYS = {"exit", "regex", "not_regex", "json", "hash"}


def _placeholders(cmd):
    n = 0
    for part in cmd:
        for m in re.finditer(r"\{(\d+)\}", part):
            n = max(n, int(m.group(1)) + 1)
    return n


def validate_read_call(cfg, read_id, args):
    spec = cfg["reads"].get(read_id)
    if spec is None:
        return f"read '{read_id}' is not in the allowlist"
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "args must be a list of strings"
    need = _placeholders(spec["cmd"])
    if len(args) != need:
        return f"read '{read_id}' takes {need} args, got {len(args)}"
    pat = re.compile(spec.get("arg_pattern", DEFAULT_ARG_PATTERN))
    for a in args:
        if not pat.match(a):
            return f"arg {a!r} rejected by arg_pattern"
    return None


def resolve_target(cfg, name):
    """'job' means wherever this job's tools run (builtin_tools.target)."""
    if name == "job":
        return cfg["builtin_tools"].get("target", "local")
    return name


def run_read(cfg, read_id, args):
    """Run an allowlisted read. Returns (exit_or_None, output)."""
    err = validate_read_call(cfg, read_id, args)
    if err:
        return targets.COULDNT_RUN, f"[librarian] {err}"
    spec = cfg["reads"][read_id]
    argv = [re.sub(r"\{(\d+)\}", lambda m: args[int(m.group(1))], part) for part in spec["cmd"]]
    return targets.run(cfg, resolve_target(cfg, spec.get("target", "local")), argv,
                       timeout=spec.get("timeout", 15), max_bytes=spec.get("max_bytes", 65536))


def _json_path(obj, path):
    for part in str(path).split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[part]
    return obj


def eval_match(match, exit_code, output):
    if exit_code is None:
        return False
    if "exit" in match and exit_code != match["exit"]:
        return False
    if "regex" in match and not re.search(match["regex"], output or "", re.M):
        return False
    if "not_regex" in match and re.search(match["not_regex"], output or "", re.M):
        return False
    if "json" in match:
        try:
            val = _json_path(json.loads(output), match["json"]["path"])
        except (ValueError, KeyError, IndexError, TypeError):
            return False
        if val != match["json"].get("equals"):
            return False
    if "hash" in match and output_hash(output) != match["hash"]:
        return False
    return True


def validate_probe(cfg, probe):
    """Structural check plus the trivial-form ban. Returns an error string or None."""
    if not isinstance(probe, dict):
        return "probe must be an object"
    for k in ("read", "args", "match"):
        if k not in probe:
            return f"probe missing '{k}'"
    err = validate_read_call(cfg, probe["read"], probe["args"])
    if err:
        return err
    m = probe["match"]
    if not isinstance(m, dict) or not m or not set(m) <= MATCH_KEYS:
        return f"match must use only {sorted(MATCH_KEYS)}"
    for rk in ("regex", "not_regex"):
        if rk in m:
            try:
                re.compile(m[rk])
            except re.error as e:
                return f"bad {rk}: {e}"
    spec = cfg["reads"][probe["read"]]
    if spec.get("trivial"):
        return f"read '{probe['read']}' is marked trivial and cannot be a probe"
    if set(m) == {"exit"} and not spec.get("exit_meaningful"):
        return f"exit-only match on '{probe['read']}' says nothing (set exit_meaningful if it does)"
    return None


def run_probe(cfg, probe):
    """Live run. Returns (outcome, exit, output)."""
    err = validate_probe(cfg, probe)
    if err:
        return COULDNT, None, f"[librarian] invalid probe: {err}"
    code, out = run_read(cfg, probe["read"], probe["args"])
    if code is None:
        return COULDNT, None, out
    return (PASS if eval_match(probe["match"], code, out) else FAIL), code, out


__all__ = ["PASS", "FAIL", "COULDNT", "run_read", "run_probe", "eval_match",
           "validate_probe", "validate_read_call", "normalize"]
