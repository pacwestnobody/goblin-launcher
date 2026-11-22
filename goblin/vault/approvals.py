"""Who may run what on which lab host, and the queue of requests waiting for the owner.

Policy file (approvals.yaml in the data dir, root-installed; same format as before):
  default: ask                      never | ask (PIN each time) | session (PIN once) | auto
  session_minutes: 30               how long a "session" approval lasts
  timeout_s: 120                    an unanswered request is denied after this
  targets: {host_id: rule}
  write_targets: [host_id, ...]     hosts that take any command; others must pass the read-only check
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

import yaml

RULES = ("never", "ask", "session", "auto")
DEFAULTS = {"default": "ask", "session_minutes": 30, "timeout_s": 120, "targets": {}, "write_targets": []}


class Pending:
    def __init__(self, target, kind, summary, agent):
        self.id = "apr_" + secrets.token_hex(6)
        self.target, self.kind, self.summary, self.agent = target, kind, summary, agent
        self.created = time.time()
        self.done = threading.Event()
        self.decision, self.by, self.remember = None, "", 0

    def public(self) -> dict:
        return {"id": self.id, "target_id": self.target, "kind": self.kind, "summary": self.summary,
                "agent": self.agent, "created": self.created, "age_s": int(time.time() - self.created)}


class Broker:
    def __init__(self, policy_path: Path, audit, session):
        self.policy_path = Path(policy_path)
        self.audit, self.session = audit, session
        self.pending: dict[str, Pending] = {}
        self.grants: dict[str, tuple[float, str]] = {}   # host -> (expiry, session id)
        self._lock = threading.Lock()

    # ---- policy ----------------------------------------------------------------
    def policy(self) -> dict:
        p = dict(DEFAULTS)
        if self.policy_path.is_file():
            p.update(yaml.safe_load(self.policy_path.read_text()) or {})
        return p

    def rule(self, host: str) -> str:
        p = self.policy()
        r = (p.get("targets") or {}).get(host, p.get("default", "ask"))
        return r if r in RULES else "ask"

    def writes_allowed(self, host: str) -> bool:
        return host in (self.policy().get("write_targets") or [])

    def save_rules(self, rules: dict, known_hosts: set[str]) -> dict:
        """Per-host rules from the Workshop. write_targets is only changed by hand (root)."""
        if not isinstance(rules, dict) or not rules:
            raise ValueError("send a map of host id to rule")
        clean = {}
        for host, rule in rules.items():
            if rule not in RULES:
                raise ValueError(f"{host}: rule must be one of {', '.join(RULES)}")
            if host not in known_hosts:
                raise ValueError(f"{host} is not a lab host in the vault")
            clean[str(host)] = rule
        raw = yaml.safe_load(self.policy_path.read_text()) if self.policy_path.is_file() else {}
        raw = raw or {}
        raw["targets"] = {**(raw.get("targets") or {}), **clean}
        tmp = self.policy_path.with_name(self.policy_path.name + ".tmp")
        tmp.write_text(yaml.safe_dump(raw, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.policy_path)
        self.audit.write("approval_rules_changed", targets=clean)
        return self.policy()

    # ---- grants -----------------------------------------------------------------
    def granted(self, host: str) -> bool:
        exp, sid = self.grants.get(host, (0, ""))
        if exp > time.time() and sid and sid == self.session.public()["session_id"]:
            return True
        self.grants.pop(host, None)
        return False

    def active_grants(self) -> dict:
        return {h: int(e - time.time()) for h, (e, _) in list(self.grants.items()) if self.granted(h)}

    # ---- requests -----------------------------------------------------------------
    def request(self, host: str, kind: str, summary: str, agent: str, no_prompt: bool = False) -> tuple[bool, str]:
        """Blocks until the owner decides (or the timeout). Returns (approved, why)."""
        rule = self.rule(host)
        if rule == "never":
            return False, f"the rule for {host} is never"
        if rule == "auto" or (rule == "session" and self.granted(host)):
            return True, rule
        if no_prompt:
            return False, "needs approval (background checks never prompt)"
        p = Pending(host, kind, summary, agent)
        with self._lock:
            self.pending[p.id] = p
        self._log("approval_requested", p)
        if not p.done.wait(timeout=float(self.policy()["timeout_s"])):
            p.decision = "timeout"
        with self._lock:
            self.pending.pop(p.id, None)
        if p.decision == "approved" and rule == "session" and p.remember:
            self.grants[host] = (time.time() + 60 * p.remember, self.session.public()["session_id"])
        self._log(f"approval_{p.decision}", p)
        return p.decision == "approved", p.decision

    def decide(self, approval_id: str, approve: bool, by: str, remember_minutes: int = 0) -> dict:
        """The caller has already checked the PIN for an approval."""
        p = self.pending.get(approval_id)
        if p is None:
            raise KeyError("no such request (it may have timed out)")
        if approve:
            p.decision, p.by = "approved", by
            if self.rule(p.target) == "session":
                p.remember = min(int(remember_minutes or self.policy()["session_minutes"]), 240)
        else:
            p.decision = "denied"
        p.done.set()
        return {"ok": True, "decision": p.decision}

    def listing(self) -> dict:
        return {"pending": [p.public() for p in list(self.pending.values())], "grants": self.active_grants()}

    def _log(self, event: str, p: Pending) -> None:
        self.audit.write(event, target_id=p.target, action=p.kind, command=p.summary, agent=p.agent,
                         approval_id=p.id, decided_by=p.by or None)
