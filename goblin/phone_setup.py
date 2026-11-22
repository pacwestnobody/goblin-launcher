"""`goblin phone setup`: add a phone from a terminal (the Workshop's "Add a phone", step by step).
`goblin phone status` shows what adb sees."""
from __future__ import annotations

import getpass
import json
import time

from . import phone
from .vault import client as vault

STEPS = """
Add a phone to Goblin Launcher
  1. On the phone: Settings → About phone → tap "Build number" 7 times (you're now a developer).
     Then Settings → System → Developer options → turn on "USB debugging".
  2. Plug it into this computer with a data cable. On the phone, tick "Always allow from this
     computer" and tap Allow.
"""


def _wait_for_phone(cfg) -> dict:
    last = ""
    while True:
        st = phone.adb_status(cfg)
        if not st["adb"]:
            raise SystemExit(st["adb_help"])
        if not st["apk"]:
            raise SystemExit("The app package isn't built: cd android && ./gradlew :app:assembleDebug")
        ready = [p for p in st["phones"] if p["state"] == "device"]
        if len(ready) == 1:
            return ready[0]
        msg = ("More than one phone is plugged in: unplug the others." if len(ready) > 1 else
               "Phone found: tap Allow on its USB debugging prompt." if any(p["state"] == "unauthorized" for p in st["phones"])
               else "Waiting for a phone…")
        if msg != last:
            print("   " + msg, flush=True)
            last = msg
        time.sleep(2)


def setup(cfg) -> int:
    print(STEPS)
    ph = _wait_for_phone(cfg)
    print(f"   Found {ph['model']}.\n")
    token = vault.main_token(cfg.path("vault", "token_file"))
    sock = (cfg.get("vault") or {}).get("socket")
    code, st = vault.call("GET", "/status", token=token, sock=sock)
    if code != 200:
        raise SystemExit(f"goblin-vault isn't reachable: {st.get('error')}")
    if not st.get("unlocked"):
        raise SystemExit("The vault is locked: unlock it first (goblin vault unlock, or the launcher).")
    keys = st.get("devices") or []
    if not keys:
        raise SystemExit("No USB key is enrolled yet: enroll one in the launcher's Workshop (USB keys).")
    print("  3. Which USB key does this phone belong to?")
    for i, k in enumerate(keys, 1):
        print(f"     {i}) {k.get('label') or 'USB key'}  ({k['device_id']})")
    pick = input("     Number [1]: ").strip() or "1"
    key = keys[int(pick) - 1]
    label = input("     Name for the phone [phone]: ").strip() or "phone"
    while True:
        pin = getpass.getpass("     The phone's own PIN (4-12 digits, not the vault PIN): ")
        if pin.isdigit() and 4 <= len(pin) <= 12 and pin == getpass.getpass("     Again: "):
            break
        print("     That didn't match or isn't 4-12 digits; try again.")
    vault_pin = getpass.getpass("     Vault PIN to confirm the pairing: ")
    try:
        print("     " + phone.pair(cfg, {"device_id": key["device_id"], "label": label, "phone_pin": pin, "pin": vault_pin})["next"])
        for line in phone.push(cfg)["log"]:
            print(f"     · {line}")
    except ValueError as e:
        raise SystemExit(f"     Stopped: {e}") from None
    print("\n  4. On the phone: type the same phone PIN once, then Turn the wheel to test it.\n"
          "     For the lab menu and room: install Termux (F-Droid), then allow it \"Display over other apps\".\n"
          "     You can switch USB debugging off again now.")
    return 0


def main(cfg, args: list[str]) -> int:
    cmd = args[0] if args else "setup"
    if cmd == "setup":
        return setup(cfg)
    if cmd == "status":
        print(json.dumps(phone.adb_status(cfg), indent=2))
        return 0
    print("usage: goblin phone [setup|status]")
    return 2
