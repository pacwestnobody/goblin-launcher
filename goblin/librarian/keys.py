"""Place keys: computed by code, never by the model.

key = key_v1 | job_kind | tool_family | artifact_id | fingerprint(error)
The fingerprint strips what changes between two runs of the same failure
(line numbers, timestamps, hex addresses, home paths, pids, durations)
and hashes what remains.
"""
import hashlib
import re

from . import KEY_VERSION

_NORMALIZERS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<TIME>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<ADDR>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<HEX>"),
    (re.compile(r"(?:/home/[^/\s]+|/Users/[^/\s]+|/root)(?=[/\s'\"]|$)"), "<HOME>"),
    (re.compile(r"\bline \d+\b", re.I), "line <N>"),
    (re.compile(r"(\.\w{1,5}):\d+(?::\d+)?"), r"\1:<N>"),
    (re.compile(r"\bpid[ =:]?\d+\b", re.I), "pid <N>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds)\b"), "<DUR>"),
    (re.compile(r"[ \t]+"), " "),
]


def normalize(text: str, limit=4000) -> str:
    t = (text or "")[:limit * 2]
    for rx, rep in _NORMALIZERS:
        t = rx.sub(rep, t)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    return "\n".join(lines)[:limit]


def fingerprint(error_text: str) -> str:
    return hashlib.sha256(normalize(error_text).encode()).hexdigest()[:16]


def output_hash(text: str) -> str:
    return hashlib.sha256(normalize(text, limit=65536).encode()).hexdigest()


def make_key(job_kind, tool_family, artifact_id, error_text) -> str:
    for part, label in ((job_kind, "job_kind"), (tool_family, "tool_family"), (artifact_id, "artifact")):
        if not part or "|" in str(part):
            raise ValueError(f"{label} must be set and must not contain '|'")
    return "|".join([KEY_VERSION, job_kind, tool_family, str(artifact_id), fingerprint(error_text)])
