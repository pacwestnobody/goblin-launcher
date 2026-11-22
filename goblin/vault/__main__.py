"""goblin-vault: the vault service and its root-only maintenance commands.

  goblin-vault serve                      run the service (systemd does this)
  goblin-vault recover-pin [--device ID]  forgot the PIN: reset it with an enrolled USB key's host copy
  goblin-vault pin-host TARGET_ID         fetch and trust a lab host's SSH keys
  goblin-vault add-credential ID KIND     store a secret (asked for, never on the command line)
  goblin-vault credentials                list what the vault holds (no secret values)
  goblin-vault seed-from DIR              first install: copy an existing vault data dir in

Maintenance commands talk to the files directly, so they need the vault's user (sudo -u goblin-vault)
and work only when someone can already read the vault's data: that is the point.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("GOBLIN_VAULT_HOME", "/var/lib/goblin-vault"))
SOCK = os.environ.get("GOBLIN_VAULT_SOCKET", "/run/goblin-vault/vault.sock")


def _ask_twice(prompt: str) -> str:
    a = getpass.getpass(prompt)
    if a != getpass.getpass("Again: "):
        raise SystemExit("they don't match; nothing changed")
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="goblin-vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    r = sub.add_parser("recover-pin")
    r.add_argument("--device", default="")
    p = sub.add_parser("pin-host")
    p.add_argument("target")
    c = sub.add_parser("add-credential")
    c.add_argument("id")
    c.add_argument("kind", choices=["ssh_private_key", "password", "api_token"])
    c.add_argument("--username", default="")
    c.add_argument("--label", default="")
    c.add_argument("--key-file", help="read an SSH private key from this file")
    sub.add_parser("credentials")
    s = sub.add_parser("seed-from")
    s.add_argument("dir")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve
        serve(HOME, Path(SOCK))
        return 0

    from .crypto import VaultError
    from .service import VaultService
    v = VaultService(HOME)
    try:
        if a.cmd == "seed-from":
            v.copy_from(Path(a.dir))
            print(f"copied {a.dir} into {v.data}")
        elif a.cmd == "recover-pin":
            wraps = v.vault.device_ids()
            dev = a.device or (wraps[0] if len(wraps) == 1 else "")
            if not dev:
                raise SystemExit(f"choose one with --device: {', '.join(wraps) or 'no keys enrolled'}")
            v.recover_pin(dev, _ask_twice("New vault PIN (6-12 digits): "))
            print("PIN reset. Unlock with the new PIN in Goblin Launcher.")
        elif a.cmd == "pin-host":
            from . import ssh
            t = v.targets.get(a.target) or sys.exit(f"no target {a.target}; known: {', '.join(v.targets)}")
            print(ssh.pin_host(v.data, t["host"], int(t.get("port") or 22)))
            print(f"pinned {t['host']}. Check these fingerprints against the host if you can.")
        elif a.cmd in ("add-credential", "credentials"):
            v.vault.unlock(getpass.getpass("Vault PIN: "))
            try:
                if a.cmd == "credentials":
                    for c in v.vault.listing():
                        print(f"{c['id']:32} {c['kind']:16} {c.get('username', ''):12} {c['label']}")
                else:
                    secret = Path(a.key_file).read_text() if a.key_file else getpass.getpass("Secret: ")
                    v.vault.put(a.id, a.kind, secret, label=a.label, username=a.username)
                    v.audit.write("credential_put", credential_id=a.id, kind=a.kind)
                    print(f"stored {a.id}")
            finally:
                v.vault.lock()
    except VaultError as e:
        raise SystemExit(f"error: {e}") from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
