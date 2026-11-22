"""The vault's rules, end to end over a real unix socket (SSH itself is replaced by a stub)."""
import threading
import time

import pytest
import yaml

from goblin.vault import client, server, ssh
from goblin.vault.service import VaultService

PIN = "246810"
TARGETS = [{"id": "hv1", "type": "ssh", "host": "10.0.0.2", "user": "root", "credential_id": "k",
            "allowed_commands": ["uptime", "qm"]},
           {"id": "nas", "type": "ssh", "host": "10.0.0.3", "user": "root", "credential_id": "k2"}]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, "run", lambda data, t, c, cmd, timeout=120: {"exit_code": 0, "stdout": f"ran {cmd}", "stderr": ""})
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "approvals.yaml").write_text(yaml.safe_dump(
        {"default": "ask", "timeout_s": 5, "targets": {"hv1": "session"}, "write_targets": ["nas"]}))
    v = VaultService(home, {"targets": TARGETS, "usb_host_dir": str(tmp_path / "usb")})
    sock = tmp_path / "v.sock"
    srv = server.UnixServer(str(sock), server.make_handler(v))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = v.tokens.main_token()

    def call(method, path, body=None, pin="", token=tok, **kw):
        return client.call(method, path, body, token=token, pin=pin, sock=str(sock), **kw)
    call("POST", "/init", {"pin": PIN})
    call("GET", "/status")
    yield v, call
    srv.shutdown()


def test_token_and_pin_rules(vault):
    v, call = vault
    assert call("GET", "/status", token="nope")[0] == 401
    assert call("POST", "/lock")[0] == 200
    assert call("POST", "/unlock", {"pin": ""})[0] == 403          # no PIN-less unlock over the socket
    assert call("POST", "/unlock", {"pin": "000000"})[0] == 401
    code, st = call("POST", "/unlock", {"pin": PIN})
    assert code == 200 and st["unlocked"] and st["seconds_remaining"] > 0
    assert call("POST", "/pin", {"new_pin": "135791"})[0] == 403    # change without X-Vault-Pin
    assert call("POST", "/pin", {"new_pin": "135791"}, pin=PIN)[0] == 200
    assert call("POST", "/pin-check", pin="135791")[0] == 200


def test_lockout(vault):
    v, call = vault
    for _ in range(5):
        call("POST", "/pin-check", pin="999999")
    assert call("POST", "/pin-check", pin=PIN)[0] == 429


def _approve_when_asked(call, pin=PIN, remember=0, approve=True):
    def go():
        for _ in range(100):
            pend = call("GET", "/approvals")[1]["pending"]
            if pend:
                call("POST", f"/approvals/{pend[0]['id']}", {"approve": approve, "remember_minutes": remember}, pin=pin)
                return
            time.sleep(0.05)
    t = threading.Thread(target=go)
    t.start()
    return t


def test_lab_run_approvals_and_limits(vault):
    v, call = vault
    v.vault.put("k", "ssh_private_key", "KEY")
    v.vault.put("k2", "ssh_private_key", "KEY2")
    # read-only host refuses writes outright (no prompt)
    assert call("POST", "/run", {"target_id": "hv1", "command": "qm stop 120"})[0] == 403
    # not on hv1's program list
    assert call("POST", "/run", {"target_id": "hv1", "command": "ls /"})[0] == 403
    # session rule: first call waits for an approval with the PIN, then rides the grant
    t = _approve_when_asked(call, remember=15)
    code, out = call("POST", "/run", {"target_id": "hv1", "command": "uptime"}, agent="mcp:grok-build")
    t.join()
    assert code == 200 and out["stdout"] == "ran uptime"
    assert call("POST", "/run", {"target_id": "hv1", "command": "qm list"})[0] == 200   # no prompt now
    # a wrong PIN can't approve; denial needs none
    t = _approve_when_asked(call, pin="000000")
    t2 = _approve_when_asked(call, approve=False)
    assert call("POST", "/run", {"target_id": "nas", "command": "zpool status"})[0] == 403
    t.join(); t2.join()
    # background checks never raise a prompt
    assert call("POST", "/run", {"target_id": "nas", "command": "uptime"}, no_prompt=True)[0] == 403
    events = [e["event"] for e in v.audit.tail(200)]
    assert "approval_approved" in events and "ssh_run" in events


def test_write_host_needs_full_access_grant(vault):
    v, call = vault
    v.vault.put("k2", "ssh_private_key", "KEY2")
    # nas is a write target but has no program list and no full-access grant -> refused
    assert call("POST", "/run", {"target_id": "nas", "command": "systemctl restart smbd"})[0] == 403
    (v.data / "access_grants.json").write_text('{"credentials": {"k2": {"full_access": true}}}')
    t = _approve_when_asked(call)
    code, out = call("POST", "/run", {"target_id": "nas", "command": "systemctl restart smbd; echo ok"})
    t.join()
    assert code == 200 and out["stdout"].startswith("ran systemctl")


def test_phone_pairing_and_revoke_cascade(vault):
    v, call = vault
    did, priv, pub = v.devices.new_key()
    v.devices.register(did, priv, pub, label="stick", iid=v.iid)
    code, out = call("POST", "/phones/pair", {"device_id": did, "phone_pin": "4321"}, pin=PIN)
    assert code == 200
    phone = out["token"]
    assert call("POST", "/phones/pair", {"device_id": did, "phone_pin": "4321"}, pin=PIN)[0] == 400  # one per key
    # the phone may not do launcher things
    assert call("GET", "/phones", token=phone)[0] == 403
    assert call("POST", "/run", {"target_id": "hv1", "command": "uptime"}, token=phone)[0] == 403
    assert call("POST", "/unlock", {"pin": "000000"}, token=phone)[0] == 401     # the vault PIN still rules
    # it opens the vault with its own PIN, and extends with it
    call("POST", "/lock")
    assert call("POST", "/phone/unlock", {"phone_pin": "9999"}, token=phone)[0] == 403
    code, st = call("POST", "/phone/unlock", {"phone_pin": "4321"}, token=phone)
    assert code == 200 and st["method"] == "phone"
    assert call("POST", "/extend", {"phone_pin": "4321", "minutes": 15}, token=phone)[0] == 200
    # revoking the USB key unpairs the phone, kills its token and locks a session it opened
    assert call("POST", f"/devices/{did}/revoke", pin=PIN)[0] == 200
    assert call("GET", "/status", token=phone)[0] == 401
    assert not call("GET", "/status")[1]["unlocked"]


def test_rules_editor_keeps_write_targets(vault):
    v, call = vault
    assert call("POST", "/rules", {"targets": {"hv1": "auto"}}, pin=PIN)[0] == 200
    assert call("POST", "/rules", {"targets": {"evil": "auto"}}, pin=PIN)[0] == 400
    rules = call("GET", "/rules")[1]
    assert rules["targets"]["hv1"] == "auto" and rules["write_targets"] == ["nas"]
