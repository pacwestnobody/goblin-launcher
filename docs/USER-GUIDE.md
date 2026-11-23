# Goblin Launcher user guide

1. [Requirements](#1-requirements)
2. [Install](#2-install)
3. [First run](#3-first-run)
4. [The launcher page](#4-the-launcher-page)
5. [Adding agents](#5-adding-agents) ← most people start here
6. [The room and the phone bridge](#6-the-room-and-the-phone-bridge)
7. [The vault and your lab hosts](#7-the-vault-and-your-lab-hosts)
8. [Adding a phone](#8-adding-a-phone)
9. [Path Librarian: facts, notes and route cards](#9-path-librarian-facts-notes-and-route-cards)
10. [Stack levers](#10-stack-levers)
11. [Commands](#11-commands)
12. [Troubleshooting](#12-troubleshooting)
13. [Uninstall](#13-uninstall)

---

## 1. Requirements

- Linux with systemd (user services) and a desktop browser. Developed on Ubuntu.
- Python 3.11 or newer, `openssl`, `ssh`, `curl`.
- Optional, by feature:
  - **Local models:** [Ollama](https://ollama.com).
  - **Command-line agents:** [Grok Build](https://x.ai) (`grok`) and/or [Claude Code](https://claude.com/claude-code) (`claude`).
  - **Notes search:** an Ollama embedding model (`ollama pull nomic-embed-text`) and the `knowledge` extra.
  - **Phone:** an Android phone, `adb` (`sudo apt install adb`), and the Android SDK to build the app once.
  - **Open WebUI** in Docker, if you want its lever.

## 2. Install

```bash
git clone https://github.com/<you>/goblin-launcher.git ~/goblin-launcher
cd ~/goblin-launcher
bash deploy/install.sh                  # your user: venv, `goblin` commands, user services (not started yet)
sudo bash deploy/install-vault.sh       # the vault: its own system user, root-owned code, hardened service
```

The installer copies `config.example.yaml` to `~/.config/goblin/config.yaml` if you have none.
Open it and set at least `owner`, `machine`, and one provider and seat (see [Adding agents](#5-adding-agents)).

Optional files, from `examples/`:

```bash
cp examples/librarian.example.yaml ~/.config/goblin/librarian.yaml   # Path Librarian settings
cp examples/shelf.example.yaml     ~/.config/goblin/shelf.yaml       # verified facts about your setup
```

Put the commands on your PATH and start it:

```bash
ln -s ~/goblin-launcher/.venv/bin/goblin ~/.local/bin/goblin
ln -s ~/goblin-launcher/.venv/bin/goblin-bridge ~/.local/bin/goblin-bridge
systemctl --user enable --now goblin.service
goblin open                                  # opens http://127.0.0.1:8770
```

**Trying it beside something else first:** make a copy of your config with other ports and another
`paths.state`, save it as `~/.config/goblin/trial.yaml`, and run `bash deploy/install.sh --trial`.

## 3. First run

1. **Create the vault** (once): `goblin vault init`. Choose a 6 to 12 digit PIN. Keep it safe: there is
   no way to read the vault without the PIN or an enrolled USB key.
2. **Open the page** (`goblin open`) and turn the vault wheel with your PIN. The Workshop appears.
3. **Enroll a USB key** (Workshop → USB keys). It is your way back in if you forget the PIN
   (`sudo goblin-vault-admin recover-pin`), and phones are paired under a key.
4. **Add agents** (next sections) and restart: `systemctl --user restart goblin.service`.

## 4. The launcher page

| Part | What it does |
|------|--------------|
| **Furnace dial** | Your local models. Turning it to a model loads that model on the GPU and unloads the others (one at a time). A slice can have a *mode* (a second model behind a switch, e.g. a tutor variant). **Open terminal** starts that model in Grok Build; **Seat in room** adds its seat to the next room. |
| **Aether dial** | Cloud agents (Grok Build, Claude Code, …). Same two buttons. |
| **GPU gauge** | VRAM in use (AMD cards via sysfs) and what Ollama has loaded. |
| **The Counting Room** | Pick a room or open a new one with the seated agents. Name a seat in your message to ask only that one. |
| **Vault door** | Unlock with your PIN; it locks itself after the session time. *Wind +15 min* asks for the PIN again. |
| **Pneumatic tube** | Requests waiting for your approval (an agent wants to run something on a lab host). Stamp with your PIN, optionally "remember this session". |
| **Levers** | GPU Ollama on, unload every model, whole stack off/on (guarded: pull twice). |
| **The Goblin's Notes** | Your shelf facts, each re-checked by a live probe. ✓ held, ✗ failed (withheld from agents), ? couldn't run. |
| **The Workshop** | Behind the open vault: USB keys, phones (*Add a phone*), approval rules per host, upkeep (snapshot, change PIN, phone certificate), the logbook. Every change asks for the PIN again. |
| **The Ledger** | Path Librarian's own jobs (streamed step by step), route cards, the shadow report, and sandbox controls. |

## 5. Adding agents

Agents are built from three config blocks in `~/.config/goblin/config.yaml`:

```
providers:   how to reach a model service (where, which kind, where the key is)
seats:       an agent in the room (a provider + a model + a personality)
dials:       what appears on the Furnace (local) and Aether (cloud) dials
```

After any change: `systemctl --user restart goblin.service`. Check with:

```bash
curl -s 127.0.0.1:8765/api/status | python3 -m json.tool   # every seat with ready / why not
goblin agents                                              # what the dials can launch in a terminal
```

### 5.1 Keys

Keys never go in `config.yaml`. A provider names either an environment variable or a key file:

```yaml
providers:
  openai: {type: openai, base_url: "https://api.openai.com/v1", key_env: OPENAI_API_KEY}
  groq:   {type: openai, base_url: "https://api.groq.com/openai/v1", key_file: ~/.config/goblin/keys/groq}
```

```bash
mkdir -p ~/.config/goblin/keys && chmod 700 ~/.config/goblin/keys
read -rs KEY && printf '%s\n' "$KEY" > ~/.config/goblin/keys/groq && chmod 600 ~/.config/goblin/keys/groq; unset KEY
```

For `key_env`, set the variable for the service: `systemctl --user edit goblin.service`, then
`[Service]` / `Environment=OPENAI_API_KEY=…` (or use a key file, which is simpler).

### 5.2 A local model (Ollama)

```bash
ollama pull qwen3:8b
```

```yaml
providers:
  local: {type: ollama, base_url: "http://127.0.0.1:11434"}
  # start_command: ~/start-ollama.sh     # optional: how the GPU lever / dial starts Ollama if it's down
  # stop_command:  ~/stop-ollama.sh
gpu: {provider: local, exclusive: true}  # local models on this Ollama load one at a time

seats:
  qwen:
    label: Qwen (local)
    provider: local
    model: "qwen3:8b"
    color: "#3ddc97"
    identity: "You are Qwen, a local model on this PC. Verify with tools instead of guessing."

dials:
  furnace:
    - {slot: qwen, name: Qwen, use: everyday, model: "qwen3:8b", seat: qwen, agent: "grok:local-qwen"}
```

- `seat` connects the dial slice to the room (*Seat in room*).
- `agent` is what *Open terminal* launches: `grok:<model id>` for a model defined in Grok Build's
  `~/.grok/config.toml`. Leave it out if you don't use Grok Build.
- A slice with a variant: add `mode: {label: Tutor, model: qwen-tutor, seat: qwen-tutor, agent: "grok:qwen-tutor"}`.

### 5.3 A cloud model through an API

Any OpenAI-compatible service uses `type: openai` with its `base_url`: OpenAI, OpenRouter, Groq,
Hugging Face's router, Together, Mistral, LM Studio, vLLM, llama.cpp's server, Ollama's `/v1`, …

```yaml
providers:
  openrouter: {type: openai, base_url: "https://openrouter.ai/api/v1", key_file: ~/.config/goblin/keys/openrouter}
  lmstudio:   {type: openai, base_url: "http://127.0.0.1:1234/v1"}            # no key needed
  anthropic:  {type: anthropic, key_env: ANTHROPIC_API_KEY}
  grok-proxy: {type: grok_proxy}            # xAI through the Grok CLI's sign-in (`grok login`), no key

seats:
  deepseek: {label: DeepSeek, provider: openrouter, model: "deepseek/deepseek-chat", color: "#4d6bfe"}
  claude:   {label: Claude, provider: anthropic, model: claude-sonnet-5-5, color: "#d4a27f", aliases: [claude api]}
  grok:     {label: Grok (cloud), provider: grok-proxy, model: grok-4.7, color: "#f0c14a"}
```

Extra headers for a service that needs them: `headers: {X-Title: my-app}` on the provider.
Slow services: `timeout: 600` (seconds).

### 5.4 A command-line agent (Grok Build, Claude Code)

These are full agents with their own tools; Goblin Launcher runs the real CLI. In the room each seat
keeps one session per room, so it remembers the conversation.

```yaml
cli_agents:
  grok:   {bin: ~/.grok/bin/grok, default_model: grok-4.7}
  claude: {bin: ~/.local/bin/claude}

seats:
  grok-build:  {label: Grok Build, provider: grok-cli, model: grok-4.7, color: "#f0c14a", aliases: [grok]}
  claude-code: {label: Claude Code, provider: claude-code, color: "#d97757", aliases: [claude]}

dials:
  aether:
    - {slot: grok, name: Grok, use: builder, agent: "grok:grok-4.7", seat: grok-build}
    - {slot: claude, name: Claude, use: reviewer, agent: claude-code, seat: claude-code}
    - {slot: spare, name: Spare, use: empty berth, empty: true}
```

`provider: grok-cli` / `claude-code` are built in. Terminal launches of a Grok model can carry extras:

```yaml
cli_agents:
  grok:
    profiles:
      qwen-tutor: {cwd: ~/notes/security, env: {SUDO_ASKPASS: ~/.local/bin/askpass}, path_prepend: [~/tools/bin]}
```

### 5.5 Seat options

| Key | Meaning |
|-----|---------|
| `label`, `color` | How the seat appears in the room. |
| `provider`, `model` | Which provider entry and which model. |
| `identity` | The seat's personality and ground rules (system prompt). |
| `aliases` | Other names that address it in a message ("grok, …"). Longer names win. |
| `tools` | `false` = chat only (default: real tools on this PC). |
| `lab` | `false` = no `lab_run` tool. |
| `guard` | `true` = the limits in the top-level `guard:` block apply (read-only files, files it may not touch, notes only through `librarian_lookup`). |
| `max_tokens` | Reply budget per model call (default 2048). |
| `cwd`, `env`, `path_prepend` | Working directory and environment for its shell tool. |

The room's tools for API and local seats: shell, read/list/grep files, write, replace,
`librarian_lookup` (facts + notes), `lab_run` (vault). A soft deny-list blocks obvious accidents
(`rm -rf /`, `mkfs`, reboot …) for every seat.

### 5.6 Giving other agents the librarian and the vault

Agents that run outside the room get the same help through MCP and hooks.

**Any MCP client** (Claude Code, Grok Build, Cursor, …): one stdio server with three tools,
`librarian_context`, `lab_run`, `librarian_shelf`:

```json
{ "mcpServers": { "goblin": { "command": "/home/you/goblin-launcher/.venv/bin/python",
                              "args": ["-m", "goblin", "mcp", "--agent", "my-agent"] } } }
```

Grok Build (`~/.grok/config.toml`):

```toml
[mcp_servers.goblin]
command = "/home/you/goblin-launcher/.venv/bin/python"
args = ["-m", "goblin", "mcp", "--agent", "grok-build"]
enabled = true
```

Agents launched from the dials get hooks automatically (facts before each prompt, every tool call
traced). Outside launches the hook is inert (`GOBLIN_LAUNCH` unset), so ordinary sessions are unchanged.

**OpenClaw:** add `~/goblin-launcher/integrations/openclaw` to `plugins.load.paths` and enable the
`goblin` plugin in `~/.openclaw/openclaw.json`.

### 5.7 A new kind of provider

One module in `goblin/providers/` with a class that has `chat(model, messages, tools, max_tokens,
temperature) -> Reply` (and `ready()`), registered in `TYPES` in `goblin/providers/__init__.py`.
Messages and tools come in OpenAI form; convert inside the class. See `anthropic.py` (47 lines).

## 6. The room and the phone bridge

Rooms are saved in `~/.local/share/goblin/rooms/`. `sequential` rooms answer one seat at a time and
each sees the previous answers; `parallel` rooms answer at once (local models still take turns on the GPU).

**The phone bridge** is a room that bots and scripts post into over HTTP. Authors must be listed:

```yaml
bridge:
  room_id: phone-bridge
  terminal_author: my-bot            # who `goblin room post` posts as
  authors:
    my-bot:      {label: My bot, color: "#e85d4c"}
    grok-build:  {label: Grok Build}
    claude-code: {label: Claude Code}
```

Posting and reading (from the same machine):

```bash
echo "disk on the NAS is at 91%" | goblin-bridge post my-bot
goblin-bridge read            # the whole room
goblin-bridge wait MSG_ID 30  # block until something new arrives
```

HTTP: `POST 127.0.0.1:8765/api/bridge/message {"author_id","text","client_nonce"}` (the nonce makes
retries safe), `GET /api/bridge?after_id=…`, `GET /api/bridge/wait?after_id=…&timeout_s=25`.

**Waking agents:** with `bridge.wake.enabled: true` and `goblin-bridge-wake.service` running, a post by
one of `wake.authors` wakes Grok Build (or whichever agent the text names) headlessly with full tools
on this PC; it posts progress and the result back. Agents hand off with `@claude` / `@grok`, up to
`max_hops` per human post. Each message is marked handled before an agent runs, so nothing repeats.

```bash
systemctl --user enable --now goblin-bridge-wake.service
```

## 7. The vault and your lab hosts

goblin-vault keeps credentials in an encrypted file (AES-256-GCM; the key is wrapped by your PIN and
by each USB key). It runs as its own system user, so nothing you or your agents run can read it.
Agents ask it to run a command; it shows you the request, and runs it after your PIN.

**Add a lab host** (as root):

1. Describe it in `/var/lib/goblin-vault/config.yaml`, then `sudo systemctl restart goblin-vault`:
   ```yaml
   targets:
     - {id: nas-ssh, type: ssh, host: 192.168.1.20, user: root, port: 22, credential_id: nas-key,
        allowed_commands: [zfs, zpool, df, uptime, systemctl]}
   ```
2. Trust its SSH host key: `sudo goblin-vault-admin pin-host nas-ssh` (compare the fingerprints).
3. Store the key: `sudo goblin-vault-admin add-credential nas-key ssh_private_key --key-file ~/nas_ed25519 --username root`
   (asks for the vault PIN). Put the public half in the host's `authorized_keys`.
4. Describe it for agents in `config.yaml` so the `lab_run` tool lists it:
   `lab_hosts: {nas-ssh: "NAS (192.168.1.20), root: ZFS pools and shares"}`.

**Who may knock** (Workshop → Who may knock), per host:

| Rule | Meaning |
|------|---------|
| Never | refused |
| Ask me | your PIN every time |
| Ask once per session | your PIN once (tick "remember"), then free until the vault locks |
| Always allow | no prompt while the vault is open |

**Read-only vs. full access** (root-only on purpose, in `/var/lib/goblin-vault/data/`):
- By default a host only takes read-only commands that are also on its `allowed_commands` list.
- To let agents change things on a host, list it in `approvals.yaml` → `write_targets: [nas-ssh]` and
  give its credential full access in `access_grants.json` → `{"credentials": {"nas-key": {"full_access": true}}}`.

**Forgot the PIN:** `sudo goblin-vault-admin recover-pin` (uses an enrolled USB key's copy on this PC).
**Backups:** Workshop → Upkeep → Snapshot writes an archive to `/var/lib/goblin-vault/backups/` (treat it as secret).

## 8. Adding a phone

Build the app once: `cd android && ./gradlew :app:assembleDebug` (needs the Android SDK).

Then open the vault → Workshop → Phones → **Add a phone**, and follow it:

1. On the phone, turn on Developer options (tap *Build number* 7×) and *USB debugging*.
2. Plug it in and tap *Allow* on "Allow USB debugging?". The lamp turns green.
3. Pick the USB key it belongs to, name it, choose its own PIN (4 to 12 digits, not the vault PIN),
   and press **Pair and install**. The app is installed with the pairing inside it.
4. On the phone, type the same phone PIN once. Done; USB debugging can go off again.

`goblin phone setup` does the same in a terminal. The phone talks to `phone.bind:phone.port` over HTTPS
with a pinned certificate, only from the networks in `phone.allow` (your LAN, your VPN):

```yaml
phone: {enabled: true, bind: 192.168.1.10, port: 8771, allow: [192.168.1.0/24, 10.8.0.0/24], ssh_user: you}
```

The app approves requests, opens/winds/locks the vault, and (with Termux) opens a lab menu and the
room over SSH. For that, use `deploy/phone/phone-gate` as the forced command on the phone's SSH key:
`command="/home/you/bin/phone-gate",restrict ssh-ed25519 AAAA… phone` in `~/.ssh/authorized_keys`.
Termux also needs *Display over other apps* in Android's settings to open a terminal for the app.

## 9. Path Librarian: facts, notes and route cards

**Shelf** (`~/.config/goblin/shelf.yaml`): short facts about your setup, each with a probe that is
re-run every few minutes. Only facts that passed reach agents. Probes may only use the allowlisted
`reads:` in `librarian.yaml`.

**Notes**: point `paths.knowledge` at a folder of Markdown notes and build the index:

```bash
pip install -e ".[knowledge]"     # inside the venv
ollama pull nomic-embed-text
goblin librarian index            # re-run after editing notes (hidden folders are skipped)
```

Relevant notes are added to an agent's turn automatically; agents can also ask (`librarian_lookup`,
`librarian_context`). Add your own names for things in `knowledge.synonyms`.

**Route cards**: after a hard job, a writer model turns the tool trace into a small card (the route
that worked, the tempting dead end, probes); a code checker accepts or rejects it.

Cards come from two kinds of job. **Ledger jobs** record their kind, dead end and finish as they run.
**Terminal sessions** (Grok Build, Claude Code, any agent wired through the hooks or MCP) are read when
they end: if a shell command matching one of your allowlisted `reads` failed, was run again after an
attempt and still failed, and later passed, the session becomes a card candidate with the matching job
kind (`systemctl` status → `service_start_fix`, `py_import`/`pip_show` → `python_dep_fix`,
`ollama_tags` → `ollama_fix`, ping/dns/http reads → `network_diag`). Anything less is left alone.
Candidates wait under **Ledger → Route cards** until you press **Write them now**, which loads the writer
model (on a local GPU, after unloading anything else); they are written straight away only if the writer
model is already loaded. `goblin librarian report` shows how many are waiting. On a repeat job the
card is offered only if its probe says it applies. Stages (`goblin librarian mode`):
`shadow` (count only) → `store` (write cards) → `live` (inject, with a holdout share). Watch
`goblin librarian report` before moving on. Jobs run from the Ledger tab or `goblin librarian run`.

## 10. Stack levers

```yaml
stack:
  units: [my-agent-gateway.service, notes-refresh.timer]   # user units switched off/on with the stack
  webui: {url: "http://127.0.0.1:3000/", container: open-webui}
  watch: {Gateway: 18789}                                   # ports shown as lights
```

*Whole stack off* stops and disables the units, stops the WebUI container, unloads every model and
stops the GPU Ollama. *On* reverses it.

## 11. Commands

| Command | Does |
|---------|------|
| `goblin open` | open the page (starts the service if needed) |
| `goblin vault init / status / unlock / lock / extend / approvals / approve ID / deny ID` | the vault from a terminal |
| `goblin room read [N] / post TEXT / wait [S]` | the phone bridge from a terminal |
| `goblin agents`, `goblin launch ID [--cwd DIR]` | list / launch dial agents in a terminal |
| `goblin phone setup / status` | add a phone over USB |
| `goblin mcp --agent NAME` | the MCP server for other agents |
| `goblin librarian index / shelf / cards / report / mode / run / models / add-model` | Path Librarian |
| `goblin-bridge post AUTHOR / read / wait` | for bots |
| `sudo goblin-vault-admin recover-pin / pin-host ID / add-credential … / credentials` | root-only vault upkeep |

## 12. Troubleshooting

| Symptom | Check |
|---------|-------|
| Page doesn't open | `systemctl --user status goblin.service`, `journalctl --user -u goblin.service -n 50` |
| Vault door says "isn't answering" | `systemctl status goblin-vault`, `journalctl -u goblin-vault -n 50` |
| A seat says "isn't ready" | `curl -s 127.0.0.1:8765/api/status` shows why (no key, binary missing, Ollama down) |
| `lab_run` refused | the host's rule, read-only vs `write_targets`, `allowed_commands`, host key pinned? The Workshop logbook shows each decision |
| Shelf fact shows "?" | its probe couldn't run (host off, needs a session approval for vault-backed reads) |
| Phone can't connect | same LAN/VPN, `phone.allow`, firewall allows `phone.port`; after *Rotate certificate* pair again |
| Termux button does nothing | Termux from F-Droid, *Display over other apps* allowed, app has the Run-command permission |
| GPU fills up | only one Furnace model at a time; *Bank the fire* unloads everything |

## 13. Uninstall

```bash
systemctl --user disable --now goblin.service goblin-bridge-wake.service
rm ~/.config/systemd/user/goblin*.service ~/.local/bin/goblin ~/.local/bin/goblin-bridge
sudo systemctl disable --now goblin-vault goblin-usb-scan.timer goblin-usb-enroll.path
sudo rm -rf /opt/goblin-vault /etc/systemd/system/goblin-* /usr/local/sbin/goblin-* /var/lib/goblin-usb
# your data: ~/.config/goblin, ~/.local/share/goblin, and (secret!) /var/lib/goblin-vault
```
