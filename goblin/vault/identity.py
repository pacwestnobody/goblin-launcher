"""Who the vault is (install id) and which USB keys belong to it.

Data-dir files (same names and formats as before, so an existing data dir works):
  install.json + install_private.pem     this install's id (binds device wraps to this machine)
  host_kek                               32-byte key that encrypts the host copies of device keys
  devices/<id>.json                      a USB key's public record
  dev-keys/<id>/device_private.pem.aesgcm   the host copy of its private key ("GHKM1" + nonce + AES-GCM)
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import VaultError, write_private

MIRROR_MAGIC = b"GHKM1"
KEY_PROTOCOL = "griphook-v1"          # written on the stick; part of the on-disk format
USB_VOLUME = "GRIPHOOK"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pem_pair():
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def install_id(data: Path) -> str:
    """Load this install's id, creating install.json + its key on first run."""
    meta, priv = data / "install.json", data / "install_private.pem"
    if meta.is_file():
        return json.loads(meta.read_text())["install_id"]
    key, pub = _pem_pair()
    iid = "inst_" + secrets.token_hex(16)
    write_private(priv, key)
    write_private(meta, json.dumps({"install_id": iid, "created_at": now_iso(), "schema_version": 1,
                                    "public_key_pem": pub, "algorithm": "Ed25519"}, indent=2) + "\n")
    return iid


class Devices:
    def __init__(self, data: Path):
        self.data = Path(data)
        (self.data / "devices").mkdir(parents=True, exist_ok=True)
        (self.data / "dev-keys").mkdir(parents=True, exist_ok=True)

    # ---- host copies of private keys ----------------------------------------------
    def _host_kek(self) -> bytes:
        p = self.data / "host_kek"
        if not p.is_file():
            write_private(p, secrets.token_bytes(32))
        key = p.read_bytes()
        if len(key) != 32:
            raise VaultError("host_kek is damaged")
        return key

    def private_pem(self, device_id: str) -> bytes:
        rec = self.get(device_id)
        if rec.get("revoked"):
            raise VaultError("that key is revoked")
        base = self.data / (rec.get("dev_key_path") or f"dev-keys/{device_id}") / "device_private.pem"
        enc = base.with_name(base.name + ".aesgcm")
        if enc.is_file():
            blob = enc.read_bytes()
            if not blob.startswith(MIRROR_MAGIC):
                raise VaultError("unknown key format")
            n = len(MIRROR_MAGIC)
            return AESGCM(self._host_kek()).decrypt(blob[n:n + 12], blob[n + 12:], MIRROR_MAGIC)
        if base.is_file():                       # old plaintext copy: encrypt it now
            pem = base.read_bytes()
            self._store_private(device_id, pem)
            base.unlink()
            return pem
        raise VaultError(f"no host copy of key {device_id}")

    def _store_private(self, device_id: str, pem: bytes) -> None:
        d = self.data / "dev-keys" / device_id
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        nonce = secrets.token_bytes(12)
        write_private(d / "device_private.pem.aesgcm",
                      MIRROR_MAGIC + nonce + AESGCM(self._host_kek()).encrypt(nonce, pem, MIRROR_MAGIC))

    # ---- registry --------------------------------------------------------------
    def get(self, device_id: str) -> dict:
        p = self.data / "devices" / f"{device_id}.json"
        if not p.is_file() or "/" in device_id:
            raise VaultError(f"no key {device_id}")
        return json.loads(p.read_text())

    def all(self, include_revoked: bool = True) -> list[dict]:
        out = []
        for p in sorted((self.data / "devices").glob("*.json")):
            rec = json.loads(p.read_text())
            if include_revoked or not rec.get("revoked"):
                out.append(rec)
        return out

    def _save(self, rec: dict) -> None:
        write_private(self.data / "devices" / f"{rec['device_id']}.json", json.dumps(rec, indent=2) + "\n")

    def revoke(self, device_id: str) -> dict:
        rec = self.get(device_id)
        if not rec.get("revoked"):
            rec.update(revoked=True, revoked_at=now_iso())
            self._save(rec)
        return rec

    @staticmethod
    def new_key() -> tuple[str, bytes, str]:
        """(device_id, private PEM, public PEM) for a key not registered yet."""
        priv, pub = _pem_pair()
        return "dev_" + secrets.token_hex(12), priv, pub

    def register(self, did: str, priv: bytes, pub: str, *, label: str, iid: str, user_id: str = "admin",
                 media: dict | None = None) -> dict:
        """Store the host copy and the registry record (after the stick was written)."""
        media = media or {}
        rec = {"device_id": did, "user_id": user_id, "label": label or did, "install_id": iid,
               "created_at": now_iso(), "revoked": False, "revoked_at": None, "public_key_pem": pub,
               "backend": "usb" if media else "file", "dev_key_path": f"dev-keys/{did}",
               "media_path": media.get("disk_path", ""), "media_partition": media.get("partition_path", ""),
               "media_serial": media.get("serial", ""), "media_model": media.get("model", ""),
               "media_display_id": media.get("display_id", ""), "key_set_id": "kset_" + secrets.token_hex(8),
               "copy_index": 1, "algorithm": "Ed25519", "protocol": KEY_PROTOCOL}
        self._store_private(did, priv)
        self._save(rec)
        return rec


def public_device(rec: dict) -> dict:
    keep = ("device_id", "label", "created_at", "revoked", "revoked_at", "backend", "media_model",
            "media_serial", "media_display_id")
    return {k: rec.get(k) for k in keep}


# ---- USB sticks: the root helper does the wiping ---------------------------------------
# The vault user can't touch block devices. A root helper (deploy/usb/goblin-usb-*) keeps an
# inventory of plugged-in sticks and carries out one enroll job at a time.

class Usb:
    def __init__(self, host_dir: Path):
        self.dir = Path(host_dir)

    def drives(self) -> list[dict]:
        inv = self.dir / "usb-drives.json"
        if not inv.is_file():
            return []
        raw = json.loads(inv.read_text())
        drives = raw.get("drives", raw) if isinstance(raw, dict) else raw
        for d in drives:
            d["confirm_phrase"] = f"WIPE {d['path']}" if d.get("eligible_for_wipe", True) and d.get("path") else None
        return drives

    def write_key(self, *, disk_path: str, confirm: str, device_id: str, iid: str, label: str,
                  public_pem: str, private_pem: bytes, timeout: int = 300) -> dict:
        if confirm.strip() != f"WIPE {disk_path}":
            raise VaultError(f"type exactly: WIPE {disk_path}")
        if disk_path not in {d.get("path") for d in self.drives()}:
            raise VaultError("that disk is not in the USB inventory (plug it in and wait a few seconds)")
        jobs = self.dir / "jobs"
        req, resp = jobs / "request.json", jobs / "response.json"
        resp.unlink(missing_ok=True)
        write_private(req, json.dumps({"disk_path": disk_path, "confirm_wipe": confirm.strip(), "device_id": device_id,
                                       "install_id": iid, "label": label, "public_pem": public_pem,
                                       "private_pem": private_pem.decode()}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if resp.is_file():
                try:
                    data = json.loads(resp.read_text())
                except ValueError:
                    time.sleep(0.2)
                    continue
                resp.unlink(missing_ok=True)
                req.unlink(missing_ok=True)
                if not data.get("ok"):
                    raise VaultError(str(data.get("error") or "the USB helper failed"))
                return data
            time.sleep(0.2)
        req.unlink(missing_ok=True)
        raise VaultError("the USB helper did not answer (is goblin-usb-enroll.path enabled?)")
