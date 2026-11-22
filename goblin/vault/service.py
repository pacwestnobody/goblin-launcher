"""The vault's rules, in one place. server.py only turns HTTP into these calls.

Runs as the goblin-vault system user. Everything an agent can run on this machine runs as the
desktop user, and so does the launcher, so every rule that matters is enforced here:
  - every call needs a token (the launcher's main token, or a paired phone's own token);
  - opening the vault needs the PIN (a phone uses its own PIN, checked against its link);
  - every change needs the PIN again (X-Vault-Pin);
  - lab commands wait for an approval, by host rule (approvals.py);
  - secrets never leave this process: SSH runs here and only output goes back.
"""
from __future__ import annotations

import json
import re
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..readonly import is_readonly
from . import ssh
from .approvals import Broker
from .crypto import Vault, VaultError
from .identity import Devices, Usb, install_id, public_device
from .records import Audit, Grants, Links, Session, Tokens, public_link


class Denied(Exception):
    """Refused: carries an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


DEFAULT_CONFIG = {"session_minutes": 30, "session_cap_minutes": 240, "max_pin_attempts": 5,
                  "usb_host_dir": "/var/lib/goblin-usb", "targets": []}


class VaultService:
    def __init__(self, home: Path, config: dict | None = None):
        """home holds config.yaml and data/ (data/ may be an existing Griphook data dir)."""
        self.home = Path(home)
        self.data = self.home / "data"
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        cfg = dict(DEFAULT_CONFIG)
        cfg_path = self.home / "config.yaml"
        if cfg_path.is_file():
            cfg.update(yaml.safe_load(cfg_path.read_text()) or {})
        cfg.update(config or {})
        self.cfg = cfg
        self.targets = {t["id"]: t for t in cfg.get("targets") or [] if t.get("type", "ssh") == "ssh"}
        self.iid = install_id(self.data)
        self.vault = Vault(self.data / "vault.enc")
        self.devices = Devices(self.data)
        self.links = Links(self.data)
        self.tokens = Tokens(self.data)
        self.grants = Grants(self.data)
        self.audit = Audit(self.data / "audit.log")
        self.session = Session(self.data / "session.json", minutes=int(cfg["session_minutes"]),
                               cap=int(cfg["session_cap_minutes"]), max_attempts=int(cfg["max_pin_attempts"]))
        self.broker = Broker(self.data / "approvals.yaml", self.audit, self.session)
        self.usb = Usb(Path(cfg["usb_host_dir"]))
        self.tokens.main_token()

    # ---- guards -------------------------------------------------------------------
    def caller(self, token: str) -> dict:
        who = self.tokens.who(token or "")
        if who is None:
            raise Denied(401, "token required")
        return who

    def is_open(self) -> bool:
        if self.vault.unlocked and not self.session.active:
            self.vault.lock()
            self.session.close()
            self.audit.write("lock", reason="session_expired")
        return self.vault.unlocked

    def need_open(self) -> None:
        if not self.is_open():
            raise Denied(423, "the vault is locked")

    def check_pin(self, pin: str) -> None:
        """For changes: the vault PIN, with the failed-attempt lockout. Doesn't open anything."""
        if not pin:
            raise Denied(403, "PIN required")
        if self.session.locked_out:
            raise Denied(429, "too many wrong PINs; locked out (restart goblin-vault to reset)")
        try:
            self.vault.check_pin(pin)
        except VaultError:
            self.session.failed_once()
            raise Denied(403, "wrong PIN") from None
        self.session.good_pin()

    def check_phone_pin(self, who: dict, pin: str) -> dict:
        if not who.get("link_id"):
            raise Denied(403, "a phone PIN only works with that phone's token")
        if self.session.locked_out:
            raise Denied(429, "too many wrong PINs; locked out")
        try:
            link = self.links.check_pin(who["link_id"], pin or "")
        except VaultError as e:
            self.session.failed_once()
            raise Denied(403, str(e)) from None
        self.session.good_pin()
        return link

    # ---- status / open / close ---------------------------------------------------------
    def status(self) -> dict:
        self.is_open()
        return {"initialized": self.vault.exists, **self.session.public(),
                "devices": [public_device(d) for d in self.devices.all(include_revoked=False)],
                "targets": sorted(self.targets)}

    def init(self, pin: str) -> dict:
        if not re.fullmatch(r"\d{6,12}", pin or ""):
            raise Denied(400, "the PIN must be 6 to 12 digits")
        self.vault.create(pin)
        self.session.open("pin")
        self.audit.write("vault_init", install_id=self.iid)
        return self.status()

    def unlock(self, pin: str) -> dict:
        if self.session.locked_out:
            raise Denied(429, "too many wrong PINs; locked out (restart goblin-vault to reset)")
        if not pin:
            raise Denied(403, "unlocking needs the PIN (forgot it? sudo goblin-vault recover-pin)")
        try:
            self.vault.unlock(pin)
        except VaultError as e:
            self.session.failed_once()
            self.audit.write("unlock", result="fail", attempts=self.session.failed)
            raise Denied(401, str(e)) from None
        self._wrap_missing_devices()
        self.session.open("pin")
        self.audit.write("unlock", result="ok", method="pin")
        return self.status()

    def _wrap_missing_devices(self) -> None:
        have = set(self.vault.device_ids())
        for d in self.devices.all(include_revoked=False):
            if d["device_id"] not in have:
                try:
                    self.vault.wrap_device(d["device_id"], self.devices.private_pem(d["device_id"]), self.iid)
                except VaultError:
                    pass

    def lock(self, reason: str = "") -> dict:
        self.vault.lock()
        self.session.close()
        self.audit.write("lock", reason=reason or None)
        return self.status()

    def extend(self, minutes: int = 15, mode: str = "add") -> dict:
        self.need_open()
        try:
            self.session.extend(int(minutes or 15), mode if mode in ("add", "restart") else "add")
        except VaultError as e:
            raise Denied(409, str(e)) from None
        self.audit.write("session_extend", minutes=minutes, mode=mode)
        return self.status()

    # ---- phones --------------------------------------------------------------------
    def phone_unlock(self, who: dict, pin: str) -> dict:
        link = self.check_phone_pin(who, pin)
        dev = self.devices.get(link["device_id"])
        if dev.get("revoked"):
            self.links.revoke(link["link_id"])
            raise Denied(401, "this phone's USB key was revoked; pair it again")
        try:
            self.vault.unlock_with_device(dev["device_id"], self.devices.private_pem(dev["device_id"]), self.iid)
        except VaultError as e:
            raise Denied(401, str(e)) from None
        self.session.open("phone", dev["device_id"])
        self.audit.write("mobile.unlock", result="ok", link_id=link["link_id"], device_id=dev["device_id"])
        return self.status()

    def bind_check(self, who: dict) -> dict:
        lid = who.get("link_id")
        if not lid:
            raise Denied(403, "only a paired phone can check its link")
        link = self.links.get(lid)
        if link.get("revoked"):
            raise Denied(401, "this phone is unpaired")
        dev = self.devices.get(link["device_id"])
        if dev.get("revoked"):
            self.links.revoke(lid)
            raise Denied(401, "this phone's USB key was revoked")
        try:
            self.devices.private_pem(dev["device_id"])
        except VaultError as e:
            raise Denied(401, f"the key's host copy is missing: {e}") from None
        return {"ok": True, "bound": True, "link_id": lid, "device_id": dev["device_id"], "device_label": dev["label"]}

    def pair_phone(self, device_id: str, pin: str, label: str = "phone") -> dict:
        self.need_open()
        dev = self.devices.get(device_id)
        if dev.get("revoked"):
            raise Denied(400, "that USB key is revoked")
        if any(l["device_id"] == device_id for l in self.links.all()):
            raise Denied(400, "this USB key already has a phone; unpair it first")
        if device_id not in self.vault.device_ids():
            self.vault.wrap_device(device_id, self.devices.private_pem(device_id), self.iid)
        try:
            link = self.links.create(device_id=device_id, pin=pin, iid=self.iid, label=label)
        except VaultError as e:
            raise Denied(400, str(e)) from None
        secret = self.tokens.create_for_link(link)
        self.audit.write("mobile.link.create", link_id=link["link_id"], device_id=device_id)
        return {"ok": True, "link": public_link(link), "token": secret}

    def unpair(self, link_id: str) -> dict:
        self.need_open()
        link = self.links.revoke(link_id)
        n = self.tokens.revoke_where(link_id=link_id)
        self.audit.write("mobile.link.revoke", link_id=link_id, tokens_revoked=n)
        return {"ok": True, "link": public_link(link)}

    def set_phone_pin(self, link_id: str, pin: str) -> dict:
        self.need_open()
        try:
            self.links.set_pin(link_id, pin)
        except VaultError as e:
            raise Denied(400, str(e)) from None
        self.audit.write("mobile_pin_changed", link_id=link_id)
        return {"ok": True}

    def phones(self) -> list[dict]:
        return [public_link(l) for l in self.links.all(include_revoked=self.is_open())]

    # ---- USB keys ------------------------------------------------------------------
    def enroll_usb(self, disk_path: str, confirm: str, label: str = "") -> dict:
        self.need_open()
        did, priv, pub = Devices.new_key()
        drive = next((d for d in self.usb.drives() if d.get("path") == disk_path), {})
        label = label or drive.get("model") or "USB key"
        try:
            media = self.usb.write_key(disk_path=disk_path, confirm=confirm, device_id=did, iid=self.iid,
                                       label=label, public_pem=pub, private_pem=priv)
        except VaultError as e:
            raise Denied(400, str(e)) from None
        rec = self.devices.register(did, priv, pub, label=label, iid=self.iid,
                                    media={**drive, **media, "disk_path": disk_path})
        self.vault.wrap_device(did, priv, self.iid)
        self.audit.write("device_enroll_usb", device_id=did, label=label, media_serial=rec.get("media_serial"))
        return {"ok": True, "device": public_device(rec)}

    def revoke_device(self, device_id: str) -> dict:
        self.need_open()
        rec = self.devices.revoke(device_id)
        killed = self.links.revoke_for_device(device_id)
        n = self.tokens.revoke_where(device_id=device_id)
        if self.session.device_id == device_id:
            self.lock("parent_device_revoked")
        self.audit.write("device_revoke", device_id=device_id, phones_unpaired=len(killed), tokens_revoked=n)
        return {"ok": True, "device": public_device(rec), "phones_unpaired": killed}

    # ---- lab commands ------------------------------------------------------------------
    def run(self, target_id: str, command: str, agent: str, timeout: int = 120, no_prompt: bool = False) -> dict:
        self.need_open()
        target = self.targets.get(target_id)
        if not target:
            raise Denied(404, f"no lab host {target_id!r}; hosts: {', '.join(sorted(self.targets))}")
        command = str(command or "").strip()
        if not command:
            raise Denied(400, "empty command")
        if not self.broker.writes_allowed(target_id) and not is_readonly(command):
            raise Denied(403, "not a read-only command, and this host is read-only")
        full = self.grants.full(target["credential_id"])
        if not ssh.allowed(target, command, full):
            raise Denied(403, f"{command.split()[0]} is not on {target_id}'s program list")
        ok, why = self.broker.request(target_id, "ssh_run", command[:500], agent or "unknown", no_prompt=no_prompt)
        if not ok:
            raise Denied(403, f"not approved ({why})")
        self.need_open()
        try:
            out = ssh.run(self.data, target, self.vault.credential(target["credential_id"]), command,
                          timeout=max(5, min(int(timeout or 120), 600)))
        except VaultError as e:
            self.audit.write("ssh_run", target_id=target_id, command=command, result="error", error=str(e), agent=agent)
            raise Denied(400, str(e)) from None
        self.audit.write("ssh_run", target_id=target_id, command=command, exit_code=out["exit_code"],
                         agent=agent, full_access=full)
        return {"target_id": target_id, "command": command, **out}

    def decide(self, who: dict, approval_id: str, approve: bool, pin: str = "", phone_pin: str = "",
               remember_minutes: int = 0) -> dict:
        by = ""
        if approve:
            if phone_pin:
                by = "phone:" + self.check_phone_pin(who, phone_pin)["link_id"]
            else:
                self.check_pin(pin)
                by = "vault PIN"
        try:
            return self.broker.decide(approval_id, approve, by, remember_minutes)
        except KeyError as e:
            raise Denied(404, str(e).strip("'")) from None

    # ---- workshop ------------------------------------------------------------------
    def rules(self) -> dict:
        p = self.broker.policy()
        return {"default": p.get("default"), "targets": p.get("targets") or {},
                "write_targets": p.get("write_targets") or [], "hosts": sorted(self.targets),
                "choices": ["never", "ask", "session", "auto"]}

    def save_rules(self, rules: dict) -> dict:
        self.need_open()
        try:
            self.broker.save_rules(rules, set(self.targets))
        except ValueError as e:
            raise Denied(400, str(e)) from None
        return self.rules()

    def change_pin(self, new_pin: str) -> dict:
        self.need_open()
        if not re.fullmatch(r"\d{6,12}", new_pin or ""):
            raise Denied(400, "the new PIN must be 6 to 12 digits")
        self.vault.set_pin(new_pin)
        self.audit.write("pin_changed")
        return {"ok": True}

    def credentials(self) -> list[dict]:
        self.need_open()
        return self.vault.listing()

    def backup(self) -> dict:
        self.need_open()
        dest = self.home / "backups"
        dest.mkdir(exist_ok=True, mode=0o700)
        out = dest / f"goblin-vault-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            tar.add(self.data, arcname="data")
        out.chmod(0o600)
        self.audit.write("backup.create", path=str(out))
        return {"ok": True, "path": str(out), "note": "secret: holds the vault and the key that opens USB key copies"}

    def log(self, limit: int = 60) -> list[dict]:
        self.need_open()
        return self.audit.tail(limit)

    # ---- root-only (goblin-vault CLI, never over the socket) ------------------------------
    def recover_pin(self, device_id: str, new_pin: str) -> None:
        if not re.fullmatch(r"\d{6,12}", new_pin or ""):
            raise VaultError("the new PIN must be 6 to 12 digits")
        self.vault.unlock_with_device(device_id, self.devices.private_pem(device_id), self.iid)
        try:
            self.vault.set_pin(new_pin)
        finally:
            self.vault.lock()
        self.session.good_pin()
        self.audit.write("pin_reset_by_device", device_id=device_id)

    def copy_from(self, src_home: Path) -> None:
        """Seed an empty vault home with another data dir (first install / migration)."""
        if (self.data / "vault.enc").exists():
            raise VaultError("this vault already has data")
        src = Path(src_home)
        src = src / "data" if (src / "data" / "vault.enc").is_file() else src
        shutil.copytree(src, self.data, dirs_exist_ok=True)


def dump(obj) -> bytes:
    return json.dumps(obj, default=str).encode()
