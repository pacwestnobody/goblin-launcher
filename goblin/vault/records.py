"""Small record stores in the vault data dir (formats unchanged, so an existing data dir works).

  mobile_links.json    paired phones: link id, parent USB key, scrypt hash of the phone PIN
  api_tokens.json      tokens by SHA-256 hash; `token` holds the main (bootstrap) token
  access_grants.json   per-credential full-access switch (lifts a host's program list)
  audit.log            one JSON line per event, never a secret
  session.json         failed-PIN counter (the unlocked state itself is never written down)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .crypto import VaultError, pin_key, write_private
from .identity import now_iso


def _load(path: Path, key: str) -> list:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    items = raw.get(key, []) if isinstance(raw, dict) else raw
    return [i for i in items if isinstance(i, dict)]


def _save(path: Path, key: str, items: list) -> None:
    write_private(path, json.dumps({"version": 1, key: items}, indent=2) + "\n")


# ---- phone links --------------------------------------------------------------------
class Links:
    """A phone paired under a USB key. Its PIN unlocks via the parent key's host copy."""

    def __init__(self, data: Path):
        self.path = Path(data) / "mobile_links.json"

    def all(self, include_revoked: bool = False) -> list[dict]:
        rows = _load(self.path, "links")
        return [r for r in rows if include_revoked or not r.get("revoked")]

    def get(self, link_id: str) -> dict:
        for r in _load(self.path, "links"):
            if r.get("link_id") == link_id:
                return r
        raise VaultError(f"no phone link {link_id}")

    def _update(self, link_id: str, **changes) -> dict:
        rows = _load(self.path, "links")
        for r in rows:
            if r.get("link_id") == link_id:
                r.update(changes)
                _save(self.path, "links", rows)
                return r
        raise VaultError(f"no phone link {link_id}")

    @staticmethod
    def _hash(pin: str, salt: bytes) -> str:
        return pin_key(pin, salt).hex()

    def create(self, *, device_id: str, pin: str, iid: str, label: str = "") -> dict:
        _check_phone_pin(pin)
        salt = secrets.token_bytes(16)
        rec = {"link_id": "link_" + secrets.token_hex(8), "device_id": device_id, "user_id": "admin",
               "label": label or "phone", "phone_label": label or "phone", "client_kind": "phone",
               "created_at": now_iso(), "revoked": False, "revoked_at": None, "pin_salt_hex": salt.hex(),
               "pin_hash_hex": self._hash(pin, salt), "install_id": iid}
        _save(self.path, "links", _load(self.path, "links") + [rec])
        return rec

    def check_pin(self, link_id: str, pin: str) -> dict:
        r = self.get(link_id)
        if r.get("revoked"):
            raise VaultError("this phone is unpaired")
        got = self._hash(pin.strip(), bytes.fromhex(r["pin_salt_hex"]))
        if not hmac.compare_digest(got, r["pin_hash_hex"]):
            raise VaultError("wrong phone PIN")
        return r

    def set_pin(self, link_id: str, pin: str) -> None:
        _check_phone_pin(pin)
        if self.get(link_id).get("revoked"):
            raise VaultError("that phone is unpaired")
        salt = secrets.token_bytes(16)
        self._update(link_id, pin_salt_hex=salt.hex(), pin_hash_hex=self._hash(pin, salt))

    def revoke(self, link_id: str) -> dict:
        return self._update(link_id, revoked=True, revoked_at=now_iso())

    def revoke_for_device(self, device_id: str) -> list[str]:
        rows = _load(self.path, "links")
        hit = [r for r in rows if r.get("device_id") == device_id and not r.get("revoked")]
        for r in hit:
            r.update(revoked=True, revoked_at=now_iso())
        if hit:
            _save(self.path, "links", rows)
        return [r["link_id"] for r in hit]


def _check_phone_pin(pin: str) -> None:
    if not (pin.isdigit() and 4 <= len(pin) <= 12):
        raise VaultError("the phone PIN must be 4 to 12 digits")


def public_link(r: dict) -> dict:
    return {k: r.get(k) for k in ("link_id", "device_id", "phone_label", "created_at", "revoked", "revoked_at")}


# ---- API tokens --------------------------------------------------------------------
def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Tokens:
    """The main token (file `token`, read by the launcher) and one token per paired phone."""

    def __init__(self, data: Path):
        self.data = Path(data)
        self.path = self.data / "api_tokens.json"

    def main_token(self) -> str:
        p = self.data / "token"
        if not p.is_file():
            write_private(p, secrets.token_urlsafe(32) + "\n")
        return p.read_text().strip()

    def who(self, secret: str) -> dict | None:
        """None for a bad token; {"kind": "main"} or the phone token record."""
        if not secret:
            return None
        if hmac.compare_digest(sha(secret), sha(self.main_token())):
            return {"kind": "main", "token_id": "main", "link_id": ""}
        h = sha(secret)
        for r in _load(self.path, "tokens"):
            if not r.get("revoked") and hmac.compare_digest(r.get("token_hash", ""), h):
                return r
        return None

    def create_for_link(self, link: dict) -> str:
        secret = secrets.token_urlsafe(32)
        rec = {"token_id": "tok_" + secrets.token_hex(8), "label": link.get("phone_label") or "phone",
               "token_hash": sha(secret), "created_at": now_iso(), "revoked": False, "revoked_at": None,
               "user_id": "admin", "link_id": link["link_id"], "device_id": link["device_id"], "kind": "phone",
               "last_used_at": None}
        _save(self.path, "tokens", _load(self.path, "tokens") + [rec])
        return secret

    def revoke_where(self, **match) -> int:
        rows = _load(self.path, "tokens")
        n = 0
        for r in rows:
            if not r.get("revoked") and r.get("kind") != "bootstrap" and all(r.get(k) == v for k, v in match.items()):
                r.update(revoked=True, revoked_at=now_iso())
                n += 1
        if n:
            _save(self.path, "tokens", rows)
        return n


# ---- full-access grants --------------------------------------------------------------
class Grants:
    def __init__(self, data: Path):
        self.path = Path(data) / "access_grants.json"

    def full(self, credential_id: str) -> bool:
        try:
            return bool(json.loads(self.path.read_text())["credentials"][credential_id]["full_access"])
        except (OSError, ValueError, KeyError, TypeError):
            return False


# ---- audit ---------------------------------------------------------------------------
REDACT = {"password", "pin", "new_pin", "mobile_pin", "secret", "private_key", "private_pem", "passphrase",
          "token", "api_token", "ciphertext", "material"}


class Audit:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        rec = {"ts": now_iso(), "event": event, **{k: v for k, v in fields.items() if v is not None and k not in REDACT}}
        line = json.dumps(rec, default=str, ensure_ascii=False)
        with self._lock:
            new = not self.path.exists()
            with self.path.open("a") as f:
                f.write(line + "\n")
            if new:
                self.path.chmod(0o600)

    def tail(self, limit: int = 60) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text().splitlines()[-max(1, min(limit, 2000)):]:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


# ---- unlock session --------------------------------------------------------------------
class Session:
    """How long the vault stays open. Only the failed-PIN counter is kept on disk."""

    def __init__(self, path: Path, *, minutes: int = 30, cap: int = 240, max_attempts: int = 5,
                 max_extends: int = 20):
        self.path = Path(path)
        self.minutes, self.cap, self.max_attempts, self.max_extends = minutes, cap, max_attempts, max_extends
        self.started = self.expires = None
        self.method = self.device_id = None
        self.extends = 0
        try:
            self.failed = int(json.loads(self.path.read_text()).get("failed_attempts") or 0)
        except (OSError, ValueError):
            self.failed = 0

    def _save(self) -> None:
        write_private(self.path, json.dumps({"failed_attempts": self.failed}))

    @property
    def active(self) -> bool:
        return self.expires is not None and datetime.now(timezone.utc) < self.expires

    @property
    def locked_out(self) -> bool:
        return self.failed >= self.max_attempts

    def failed_once(self) -> None:
        self.failed += 1
        self._save()

    def good_pin(self) -> None:
        if self.failed:
            self.failed = 0
            self._save()

    def open(self, method: str, device_id: str | None = None) -> None:
        self.started = datetime.now(timezone.utc)
        self.expires = self.started + timedelta(minutes=min(self.minutes, self.cap))
        self.method, self.device_id, self.extends = method, device_id, 0
        self.good_pin()

    def close(self) -> None:
        self.started = self.expires = self.method = self.device_id = None
        self.extends = 0

    def extend(self, minutes: int = 15, mode: str = "add") -> None:
        if not self.active:
            raise VaultError("the vault is locked")
        if self.extends >= self.max_extends:
            raise VaultError(f"already extended {self.max_extends} times; lock and unlock again")
        now = datetime.now(timezone.utc)
        base = self.expires if mode == "add" else now
        new = base + timedelta(minutes=max(1, int(minutes)))
        self.expires = min(new, now + timedelta(minutes=self.cap))
        self.extends += 1

    def seconds_left(self) -> int:
        return max(0, int((self.expires - datetime.now(timezone.utc)).total_seconds())) if self.active else 0

    def public(self) -> dict:
        return {"unlocked": self.active, "expires_at": self.expires.isoformat() if self.active else None,
                "seconds_remaining": self.seconds_left(), "method": self.method if self.active else None,
                "can_extend": self.active and self.extends < self.max_extends, "extend_count": self.extends,
                "locked_out": self.locked_out, "session_id": self.started.isoformat() if self.active else ""}
