#!/usr/bin/env bash
# Install Goblin Launcher for your user (no sudo). The vault is separate: sudo bash deploy/install-vault.sh
#   bash deploy/install.sh           the venv, the `goblin` commands, user units (not started)
#   bash deploy/install.sh --trial   the same, then start goblin-trial.service on the spare ports
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SRC"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[knowledge]"
UNITS="$HOME/.config/systemd/user"; mkdir -p "$UNITS"
for u in goblin.service goblin-bridge-wake.service goblin-trial.service; do
  sed "s#@SRC@#$SRC#g" "deploy/systemd/$u" > "$UNITS/$u"; chmod 0644 "$UNITS/$u"     # units point at this checkout
done
systemctl --user daemon-reload
mkdir -p "$HOME/.config/goblin"
[ -f "$HOME/.config/goblin/config.yaml" ] || { cp config.example.yaml "$HOME/.config/goblin/config.yaml"; echo "wrote ~/.config/goblin/config.yaml: edit it"; }
if [ "${1:-}" = "--trial" ]; then
  systemctl --user restart goblin-trial.service
  sleep 3
  systemctl --user --no-pager --lines=5 status goblin-trial.service | head -8
  echo "Trial: http://127.0.0.1:8780/"
else
  echo "Installed. Commands: $SRC/.venv/bin/goblin (the cutover links them into ~/.local/bin)."
fi
