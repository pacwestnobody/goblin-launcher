#!/usr/bin/env bash
# Install / update goblin-vault.   sudo bash deploy/install-vault.sh [--seed-from DIR] [--targets FILE]
#
#   user    goblin-vault (system, no login)
#   code    /opt/goblin-vault        root-owned: agents (running as you) can't edit it
#   data    /var/lib/goblin-vault    0700 goblin-vault: data/ (vault, keys, links, audit), config.yaml
#   socket  /run/goblin-vault/vault.sock
#   admin   /usr/local/sbin/goblin-vault-admin   root-only maintenance, run as the vault user
#
# --seed-from DIR   copy an existing vault data dir in (only when this vault has no data yet),
#                   e.g. /var/lib/librarian-vault/data. The source is only read.
# --targets FILE    lab hosts (YAML with `targets:`), e.g. the old /var/lib/librarian-vault/config/griphook.yaml
# Re-running updates code and units; data, config and policy are left alone.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
OWNER="${SUDO_USER:?run this with sudo from your own account}"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/goblin-vault
HOME_DIR=/var/lib/goblin-vault
USB=/var/lib/goblin-usb
SEED="" TARGETS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --seed-from) SEED="$2"; shift 2 ;;
    --targets) TARGETS="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

echo "== user"
id goblin-vault >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin goblin-vault

echo "== code -> $PREFIX (root-owned)"
NEW="$PREFIX.new"; rm -rf "$NEW"; mkdir -p "$NEW/src"
cp -a "$SRC/goblin" "$SRC/pyproject.toml" "$NEW/src/"
rm -rf "$NEW/src/goblin/ui/static" "$NEW/src/goblin/android"      # the vault serves no pages
find "$NEW/src" -name __pycache__ -prune -exec rm -rf {} +
/usr/bin/python3 -m venv "$NEW/venv"
"$NEW/venv/bin/pip" install -q --upgrade pip
"$NEW/venv/bin/pip" install -q "$NEW/src"
chown -R root:root "$NEW"; chmod -R go-w "$NEW"
rm -rf "$PREFIX.old"; [ -d "$PREFIX" ] && mv "$PREFIX" "$PREFIX.old"; mv "$NEW" "$PREFIX"; rm -rf "$PREFIX.old"

echo "== data -> $HOME_DIR"
install -d -o goblin-vault -g goblin-vault -m 0700 "$HOME_DIR" "$HOME_DIR/data"
if [ -n "$SEED" ]; then
  if [ -e "$HOME_DIR/data/vault.enc" ]; then
    echo "   this vault already has data: --seed-from ignored"
  else
    [ -f "$SEED/vault.enc" ] || { echo "   no vault.enc in $SEED"; exit 1; }
    cp -a "$SEED/." "$HOME_DIR/data/"
    chown -R goblin-vault:goblin-vault "$HOME_DIR/data"; chmod -R go-rwx "$HOME_DIR/data"
    echo "   copied $SEED (the original is untouched)"
  fi
fi
if [ ! -f "$HOME_DIR/config.yaml" ]; then
  "$PREFIX/venv/bin/python" - "$HOME_DIR/config.yaml" "${TARGETS:-}" <<'PY'
import sys, yaml
out, src = sys.argv[1], sys.argv[2]
targets = []
if src:
    raw = yaml.safe_load(open(src)) or {}
    keep = ("id", "type", "host", "user", "port", "credential_id", "allowed_commands")
    targets = [{k: t[k] for k in keep if k in t} for t in raw.get("targets") or [] if t.get("type", "ssh") == "ssh"]
cfg = {"session_minutes": 30, "session_cap_minutes": 240, "max_pin_attempts": 5,
       "usb_host_dir": "/var/lib/goblin-usb", "targets": targets}
open(out, "w").write("# goblin-vault settings (no secrets). Lab hosts: id, host, user, port, credential_id.\n"
                     + yaml.safe_dump(cfg, sort_keys=False))
print(f"   config.yaml with {len(targets)} lab host(s)")
PY
  chown goblin-vault:goblin-vault "$HOME_DIR/config.yaml"; chmod 0600 "$HOME_DIR/config.yaml"
fi
[ -f "$HOME_DIR/data/approvals.yaml" ] || { printf 'default: ask\nsession_minutes: 30\ntimeout_s: 120\ntargets: {}\nwrite_targets: []\n' \
  > "$HOME_DIR/data/approvals.yaml"; chown goblin-vault:goblin-vault "$HOME_DIR/data/approvals.yaml"; chmod 0600 "$HOME_DIR/data/approvals.yaml"; }

echo "== USB key helper (root; acts only on jobs goblin-vault drops after the owner's PIN)"
install -d -o root -g goblin-vault -m 0750 "$USB"
install -d -o root -g goblin-vault -m 0770 "$USB/jobs"
install -m 0755 "$SRC/deploy/usb/goblin-usb-scan" /usr/local/sbin/goblin-usb-scan
install -m 0755 "$SRC/deploy/usb/goblin-usb-enroll" /usr/local/sbin/goblin-usb-enroll
for u in goblin-usb-enroll.path goblin-usb-enroll.service goblin-usb-scan.service goblin-usb-scan.timer; do
  install -m 0644 "$SRC/deploy/usb/$u" "/etc/systemd/system/$u"
done
install -d -m 0755 /etc/goblin
if [ ! -f /etc/goblin/usb-never-wipe ]; then
  if [ -f /etc/librarian/usb-never-wipe ]; then cp /etc/librarian/usb-never-wipe /etc/goblin/usb-never-wipe
  else install -m 0644 "$SRC/deploy/usb/usb-never-wipe.example" /etc/goblin/usb-never-wipe; fi
fi

echo "== admin wrapper"
cat > /usr/local/sbin/goblin-vault-admin <<EOF
#!/bin/sh
# Root-only vault maintenance (recover-pin, pin-host, add-credential, credentials), run as the vault user
# so its files keep the right owner.
[ "\$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
exec setpriv --reuid=goblin-vault --regid=goblin-vault --init-groups env GOBLIN_VAULT_HOME=$HOME_DIR $PREFIX/venv/bin/python -m goblin.vault "\$@"
EOF
chmod 0755 /usr/local/sbin/goblin-vault-admin

echo "== unit"
cat > /etc/systemd/system/goblin-vault.service <<EOF
[Unit]
Description=goblin-vault: lab credentials, USB keys, phone links, approvals
After=network-online.target

[Service]
User=goblin-vault
Group=goblin-vault
Environment=GOBLIN_VAULT_HOME=$HOME_DIR
Environment=GOBLIN_VAULT_SOCKET=/run/goblin-vault/vault.sock
Environment=PYTHONUNBUFFERED=1
# python -m, not the venv's console script: the venv is built in $PREFIX.new and moved, which breaks script shebangs
ExecStart=$PREFIX/venv/bin/python -m goblin.vault serve
RuntimeDirectory=goblin-vault
RuntimeDirectoryMode=0755
UMask=0077
Restart=on-failure
RestartSec=3
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$HOME_DIR $USB
PrivateTmp=true
PrivateDevices=true
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now goblin-usb-scan.timer goblin-usb-enroll.path
systemctl start goblin-usb-scan.service || true
systemctl enable goblin-vault.service
systemctl restart goblin-vault.service
for _ in $(seq 1 50); do [ -S /run/goblin-vault/vault.sock ] && break; sleep 0.2; done

echo "== the launcher's vault token -> $OWNER_HOME/.config/goblin/vault-token (0600, yours)"
install -d -o "$OWNER" -g "$OWNER" -m 0700 "$OWNER_HOME/.config/goblin"
install -o "$OWNER" -g "$OWNER" -m 0600 "$HOME_DIR/data/token" "$OWNER_HOME/.config/goblin/vault-token"

systemctl --no-pager --lines=0 status goblin-vault.service | head -3
echo "== done. Vault socket: /run/goblin-vault/vault.sock"
