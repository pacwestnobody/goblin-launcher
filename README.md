# Goblin Launcher

**A self-hosted control room for AI agents on your own Linux machine.** Run local models and cloud
agents side by side, put them in one conversation with you, let them work on your home-lab servers
without ever holding a password, and approve what they do from your phone.

> Short description (for the GitHub "About" box):
> *Self-hosted launcher for local + cloud AI agents: shared multi-agent room, PIN-gated credential
> vault for lab access, phone approvals, and a probe-verified knowledge layer. Any provider.*

![steampunk goblin theme](goblin/ui/static/img/goblin.webp)

---

## What it is

Goblin Launcher is one program (Python, a browser page, an Android app) that ties together everything
you need to use AI agents seriously on hardware you own:

- **Any model, any provider.** Local models through Ollama; cloud models through any
  OpenAI-compatible API (OpenAI, OpenRouter, Groq, Together, Mistral, LM Studio, vLLM, llama.cpp…),
  Anthropic's API, or xAI via the Grok CLI. Full command-line agents (Grok Build, Claude Code) run as
  themselves, with their own tools. Adding a provider is a few lines of config, or one small module
  for a new kind of API.
- **One GPU, no fights.** Local models load one at a time and only when you choose one, so a
  consumer card never runs out of memory because two agents grabbed it at once.
- **A room where agents talk.** You and any number of AI "seats" in one conversation. Name a seat to
  ask just that one, or let them answer in turn and build on each other. Each seat has real tools on
  your PC (shell, files, search) with guard rails you set per seat.
- **A phone bridge.** A room that bots and scripts post into over HTTP. Posts can wake a command-line
  agent headlessly; it does the work and reports back, and agents can hand off to each other.
- **A vault for lab access.** goblin-vault holds SSH keys and credentials, encrypted, under its own
  system user. Agents ask it to run a command on a lab host; you see the exact command and approve it
  with your PIN (every time, once per session, or always, per host). Agents get the output, never the key.
  Hosts are read-only by default; letting agents change things is a root-only, per-host decision.
- **Your phone as the remote.** The Android app shows approval requests, unlocks and locks the vault,
  extends the session, and opens a lab menu over SSH (with Termux). Setting up a phone is one button:
  plug it in, and the launcher pairs it and installs the app with the pairing already inside.
- **Path Librarian, the memory that checks itself.** Short facts about your setup ("the photo server
  runs in CT 105") are re-verified by live probes; only facts that hold right now reach an agent.
  Your Markdown notes are indexed and the relevant bits are added to each turn automatically. After
  hard jobs, it can learn "route cards" from the tool trace (what worked, the tempting dead end), and
  offers them on a repeat job only if a probe says they still apply.
- **A launcher page with character.** A steampunk "goblin workshop": a Furnace dial for local models,
  an Aether dial for cloud agents, a GPU gauge, the vault door, a pneumatic tube of approvals, levers
  for the whole stack, a Workshop for keys, phones and rules, and a Ledger for the librarian.

## Who it's for

People with a home lab or a capable desktop who want to use several AI agents (local and cloud)
day to day, let them help administer real machines, and stay in control of what touches what.
It runs as your user on one Linux machine; nothing is exposed beyond loopback except the phone
listener, which only answers your LAN or VPN over pinned HTTPS.

## How it fits together

```
 browser ──► goblin.service (your user) ───────────────────────────────┐
             ├─ launcher page :8770   dials, room, vault door, Workshop, Ledger
             ├─ room :8765            seats ─► providers (ollama / openai / anthropic / grok / CLIs)
             ├─ agent socket          hooks + MCP for Grok Build, Claude Code, OpenClaw, any MCP client
             ├─ Path Librarian        shelf facts (probed), notes index, route cards
             └─ phone listener :8771  pinned HTTPS, LAN/VPN only
                         │
                         ▼  unix socket, token + PIN
             goblin-vault.service (own system user, root-owned code)
             ├─ credentials (AES-256-GCM; key wrapped by your PIN and by USB keys)
             ├─ approvals, per-host rules, audit log
             └─ SSH to lab hosts (pinned host keys) ──► your servers
```

Why two services: everything an agent runs runs as you, and so does the launcher. Putting the vault
under a different user is what makes "agents never see a key" true rather than a promise.

## Quick start

```bash
git clone https://github.com/<you>/goblin-launcher.git ~/goblin-launcher && cd ~/goblin-launcher
bash deploy/install.sh                  # venv, `goblin` command, user services
sudo bash deploy/install-vault.sh       # the vault service
$EDITOR ~/.config/goblin/config.yaml    # owner, machine, providers, seats, dials (see config.example.yaml)
ln -s ~/goblin-launcher/.venv/bin/goblin ~/.local/bin/goblin
systemctl --user enable --now goblin.service
goblin vault init                       # choose your PIN
goblin open
```

The smallest useful config, one local model:

```yaml
owner: Alex
machine: workstation
providers:
  local: {type: ollama, base_url: "http://127.0.0.1:11434"}
gpu: {provider: local}
seats:
  qwen: {label: Qwen, provider: local, model: "qwen3:8b", color: "#3ddc97"}
dials:
  furnace: [{slot: qwen, name: Qwen, use: everyday, model: "qwen3:8b", seat: qwen}]
  aether: []
```

Everything else (cloud agents, Claude Code / Grok Build, lab hosts, phones, notes, the bridge) is in
the **[user guide](docs/USER-GUIDE.md)**, which has a full section on adding agents.

## Requirements

Linux with systemd, Python 3.11+, `openssl`, `ssh`, `curl`. Optional: Ollama (local models),
Grok Build / Claude Code (CLI agents), `adb` + Android SDK (phone app), Termux on the phone (lab menu),
an Ollama embedding model (notes search). Tested on Ubuntu with an AMD GPU (the gauge reads AMD sysfs;
other cards work, the gauge just shows less).

## Rules it keeps

- **Secrets stay in the vault.** No keys in config files: providers read keys from an environment
  variable or a key file you create; lab credentials live only in goblin-vault.
- **Every change needs the PIN.** Approvals, rules, phones, keys. Five wrong PINs lock it until restart.
  PIN recovery needs a USB key and root.
- **Read-only unless you say otherwise**, per host, as root.
- **One local model at a time, only when you ask.**
- **The librarian can't make things up:** its writer only sees tool traces, its checker is plain code,
  its probes are an allowlist of read-only commands, cards never contain code, and it rolls out in
  stages (shadow → store → live) so you can measure it before trusting it.
- **Web surfaces are loopback-only** and refuse cross-site requests; the phone uses a pinned certificate.

## Project layout

```
goblin/            the program: config, CLI, service, providers/, agents/, room/, ui/, vault/, librarian/
android/           the phone app (Kotlin, Jetpack Compose)
integrations/      OpenClaw plugin
deploy/            installers, systemd units, USB helper, phone SSH gate
examples/          librarian.yaml and shelf.yaml to start from
docs/USER-GUIDE.md everything about using and extending it
tests/             pytest (no real models, no network services)
```

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes goblin
cd android && ./gradlew :app:assembleDebug
```

New providers go in `goblin/providers/` (one class with `chat()` and `ready()`, registered in `TYPES`).
New page features: a route in `goblin/ui/server.py`'s `table()` plus script in `goblin/ui/static/`.
Vault rules live in exactly one place, `goblin/vault/service.py`.

## Status

Personal project, used daily on one machine. Expect rough edges outside that setup; issues and
pull requests are welcome. Android app builds as a debug APK on purpose (pairing writes into the
app's private storage with `adb run-as`).

## License

MIT, see [LICENSE](LICENSE).
