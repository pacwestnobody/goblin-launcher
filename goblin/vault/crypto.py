"""The encrypted vault file (vault.enc).

Format (unchanged from Griphook, so existing vaults open as they are):
  v2 "kek-wrap": a random 32-byte key (KEK) encrypts the credentials with AES-256-GCM.
  The KEK is wrapped twice over:
    pin_wrap      scrypt(PIN, n=2^14, r=8, p=1) -> AES-GCM(KEK)
    device_wraps  HKDF-SHA256(device private PEM, salt=install_id, info=DEVICE_WRAP_INFO) -> AES-GCM(KEK)
  v1 (legacy, read only): the credentials encrypted directly with scrypt(PIN).

The label strings below are part of the file format; they keep their old spelling on purpose.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

DEVICE_WRAP_INFO = b"griphook-device-wrap-v1"
KINDS = ("password", "ssh_private_key", "api_token")
SECRET_FIELD = {"password": "password", "ssh_private_key": "private_key", "api_token": "secret"}
PUBLIC_FIELDS = ("username", "token_id", "base_url")


class VaultError(Exception):
    pass


class VaultLocked(VaultError):
    pass


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def pin_key(pin: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(pin.encode("utf-8"))


def device_key(private_pem: bytes, install_id: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=install_id.encode("utf-8"),
                info=DEVICE_WRAP_INFO).derive(private_pem)


def seal(key: bytes, plain: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    return {"nonce": b64e(nonce), "ciphertext": b64e(AESGCM(key).encrypt(nonce, plain, None))}


def unseal(key: bytes, blob: dict) -> bytes:
    try:
        return AESGCM(key).decrypt(b64d(blob["nonce"]), b64d(blob["ciphertext"]), None)
    except (InvalidTag, KeyError, ValueError) as e:
        raise VaultError("wrong PIN or key") from e


def write_private(path: Path, data: str | bytes) -> None:
    """Atomic write, mode 600."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data.encode() if isinstance(data, str) else data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Vault:
    """vault.enc. While unlocked the KEK and credentials live only in this process."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._kek: bytes | None = None
        self.creds: dict[str, dict] | None = None   # id -> {id, kind, label, fields, meta}

    # ---- state ----------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def unlocked(self) -> bool:
        return self._kek is not None

    def envelope(self) -> dict:
        return json.loads(self.path.read_text())

    def device_ids(self) -> list[str]:
        if not self.exists:
            return []
        env = self.envelope()
        return sorted((env.get("device_wraps") or {}).keys()) if env.get("format") == "kek-wrap" else []

    def lock(self) -> None:
        self._kek, self.creds = None, None

    # ---- create / open ----------------------------------------------------------
    def create(self, pin: str) -> None:
        if self.exists:
            raise VaultError("a vault already exists")
        if len(pin) < 6:
            raise VaultError("the PIN must be at least 6 characters")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._kek, self.creds = secrets.token_bytes(32), {}
        self._write(pin_wrap=self._pin_wrap(pin), device_wraps={})

    def check_pin(self, pin: str) -> bytes:
        """The KEK if the PIN is right (VaultError otherwise). Changes nothing."""
        env = self.envelope()
        if env.get("format") != "kek-wrap":
            raise VaultError("legacy vault: open it with unlock() to upgrade it first")
        wrap = env["pin_wrap"]
        return unseal(pin_key(pin, b64d(wrap["salt"])), wrap)

    def unlock(self, pin: str) -> None:
        if not self.exists:
            raise VaultError("no vault yet")
        env = self.envelope()
        if env.get("format") != "kek-wrap":              # v1: upgrade in place
            key = pin_key(pin, b64d(env["salt"]))
            try:
                plain = AESGCM(key).decrypt(b64d(env["nonce"]), b64d(env["ciphertext"]), None)
            except InvalidTag as e:
                raise VaultError("wrong PIN") from e
            self.creds = self._decode(plain)
            self._kek = secrets.token_bytes(32)
            self._write(pin_wrap=self._pin_wrap(pin), device_wraps={})
            return
        try:
            kek = self.check_pin(pin)
        except VaultError as e:
            raise VaultError("wrong PIN") from e
        self._open(kek, env)

    def unlock_with_device(self, device_id: str, private_pem: bytes, install_id: str) -> None:
        env = self.envelope()
        wrap = (env.get("device_wraps") or {}).get(device_id)
        if not wrap:
            raise VaultError(f"device {device_id} is not enrolled on this vault")
        self._open(unseal(device_key(private_pem, install_id), wrap), env)

    def _open(self, kek: bytes, env: dict) -> None:
        plain = unseal(kek, {"nonce": env["vault_nonce"], "ciphertext": env["vault_ciphertext"]})
        self._kek, self.creds = kek, self._decode(plain)

    # ---- changes (vault must be open) -------------------------------------------
    def _need_open(self) -> bytes:
        if self._kek is None:
            raise VaultLocked("the vault is locked")
        return self._kek

    def set_pin(self, new_pin: str) -> None:
        self._need_open()
        self._write(pin_wrap=self._pin_wrap(new_pin), device_wraps=self.envelope().get("device_wraps") or {})

    def wrap_device(self, device_id: str, private_pem: bytes, install_id: str) -> None:
        kek = self._need_open()
        env = self.envelope()
        wraps = dict(env.get("device_wraps") or {})
        wraps[device_id] = {**seal(device_key(private_pem, install_id), kek),
                            "kdf": "hkdf-sha256", "info": DEVICE_WRAP_INFO.decode()}
        self._write(pin_wrap=env["pin_wrap"], device_wraps=wraps)

    def credential(self, cred_id: str) -> dict:
        self._need_open()
        if cred_id not in self.creds:
            raise VaultError(f"no credential {cred_id!r}")
        return self.creds[cred_id]

    def put(self, cred_id: str, kind: str, secret: str, *, label: str = "", username: str = "",
            meta: dict | None = None, **extra: str) -> dict:
        self._need_open()
        if kind not in KINDS:
            raise VaultError(f"kind must be one of {', '.join(KINDS)}")
        if not secret:
            raise VaultError("the secret is empty")
        fields = {SECRET_FIELD[kind]: secret, **{k: str(v) for k, v in extra.items() if v}}
        if username:
            fields["username"] = username
        self.creds[cred_id] = {"id": cred_id, "kind": kind, "label": label, "fields": fields, "meta": meta or {}}
        self.save()
        return public_view(self.creds[cred_id])

    def delete(self, cred_id: str) -> None:
        self.credential(cred_id)
        del self.creds[cred_id]
        self.save()

    def save(self) -> None:
        self._need_open()
        env = self.envelope()
        self._write(pin_wrap=env["pin_wrap"], device_wraps=env.get("device_wraps") or {})

    def listing(self) -> list[dict]:
        self._need_open()
        return [public_view(c) for c in self.creds.values()]

    # ---- file -----------------------------------------------------------------
    def _pin_wrap(self, pin: str) -> dict:
        salt = os.urandom(16)
        return {"kdf": "scrypt", "salt": b64e(salt), **seal(pin_key(pin, salt), self._kek)}

    def _write(self, *, pin_wrap: dict, device_wraps: dict) -> None:
        body = seal(self._kek, json.dumps({"version": 2, "credentials": self.creds},
                                          separators=(",", ":")).encode())
        env = {"v": 2, "format": "kek-wrap", "vault_nonce": body["nonce"], "vault_ciphertext": body["ciphertext"],
               "pin_wrap": pin_wrap, "device_wraps": device_wraps}
        write_private(self.path, json.dumps(env))

    @staticmethod
    def _decode(plain: bytes) -> dict:
        data = json.loads(plain)
        out = {}
        for cid, c in (data.get("credentials") or {}).items():
            fields = dict(c.get("fields") or {})
            if c.get("material") and SECRET_FIELD.get(c.get("kind")) not in fields:   # very old records
                fields[SECRET_FIELD.get(c["kind"], "material")] = c["material"]
            out[cid] = {"id": c.get("id", cid), "kind": c["kind"], "label": c.get("label") or "",
                        "fields": fields, "meta": dict(c.get("meta") or {})}
        return out


def secret_of(cred: dict) -> str:
    return cred["fields"].get(SECRET_FIELD.get(cred["kind"], ""), "") or cred["fields"].get("material", "")


def public_view(cred: dict) -> dict:
    f = cred["fields"]
    return {"id": cred["id"], "kind": cred["kind"], "label": cred.get("label", ""),
            **{k: f.get(k, "") for k in PUBLIC_FIELDS}, "has_secret": bool(secret_of(cred))}
