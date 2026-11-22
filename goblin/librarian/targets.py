"""Where commands run. Every tool and every probe executes through a target.

Types:
  local   subprocess on this machine
  ssh     ssh user@host <cmd>
  pct     ssh to a Proxmox host, then `pct exec <ctid> -- <cmd>` (LXC sandbox)
  docker  docker exec <container> <cmd>

Commands are always argv lists. Nothing is passed through a local shell.
Remote commands are joined with shlex.join so arguments stay quoted.
"""
import os
import shlex
import subprocess

COULDNT_RUN = None  # exit code sentinel: the command never got a real result


class TargetError(RuntimeError):
    pass


def _ssh_base(t):
    base = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={t.get('connect_timeout', 8)}"]
    if t.get("port"):
        base += ["-p", str(t["port"])]
    if t.get("identity"):
        # IdentitiesOnly: without it ssh also offers agent/default keys, and a default key
        # with full root on the host would bypass the forced-command gate entirely.
        base += ["-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(str(t["identity"]))]
    user = t.get("user", "root")
    return base + [f"{user}@{t['host']}"]


def build_argv(t, argv):
    kind = t.get("type", "local")
    if kind == "local":
        return list(argv)
    if kind == "ssh":
        return _ssh_base(t) + [shlex.join(argv)]
    if kind == "pct":
        return _ssh_base(t) + [shlex.join(["pct", "exec", str(t["ctid"]), "--"] + list(argv))]
    if kind == "docker":
        return ["docker", "exec", "-i", t["container"]] + list(argv)
    raise TargetError(f"unknown target type '{kind}'")


def run(cfg, target_name, argv, timeout=30, stdin=None, max_bytes=65536, agent=None):
    """Return (exit_code_or_None, output_text). None means couldn't-run."""
    t = cfg["targets"].get(target_name)
    if t is None:
        return COULDNT_RUN, f"[librarian] unknown target '{target_name}'"
    if t.get("type") == "vault":
        return _run_vault(cfg, t, argv, timeout, max_bytes, agent)
    try:
        full = build_argv(t, argv)
    except (TargetError, KeyError) as e:
        return COULDNT_RUN, f"[librarian] bad target '{target_name}': {e}"
    try:
        p = subprocess.run(full, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return COULDNT_RUN, f"[librarian] timeout after {timeout}s"
    except FileNotFoundError as e:
        return COULDNT_RUN, f"[librarian] not found: {e}"
    except OSError as e:
        return COULDNT_RUN, f"[librarian] os error: {e}"
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    if len(out) > max_bytes:
        out = out[:max_bytes] + "\n[librarian] output truncated"
    # ssh uses 255 for its own connection failures
    if t.get("type") in ("ssh", "pct") and p.returncode == 255:
        return COULDNT_RUN, out
    return p.returncode, out


def _run_vault(cfg, t, argv, timeout, max_bytes, agent):
    """A lab host behind goblin-vault: the vault runs it after the owner approves."""
    from ..vault import client
    g = cfg.goblin or {}
    v = g.get("vault") or {}
    token = client.main_token(g.path("vault", "token_file")) if g else client.main_token()
    code, data = client.call("POST", "/run", {"target_id": t["vault_target"], "command": shlex.join(argv),
                                              "timeout": min(int(timeout), 600)},
                             token=token, sock=v.get("socket"), timeout=int(timeout) + 300,
                             agent=agent or "librarian", no_prompt=bool(t.get("no_prompt")))
    if code == 0:
        return COULDNT_RUN, f"[librarian] {data.get('error')}"
    if code == 423:
        return COULDNT_RUN, "[librarian] the vault is locked"
    if code == 403:
        if t.get("no_prompt"):
            return COULDNT_RUN, f"[librarian] {data.get('error', 'needs approval')}"
        return 1, f"[librarian] {data.get('error', 'not approved')}"
    if code >= 400:
        return 1, f"[librarian] vault error {code}: {data.get('error') or data}"
    out = (data.get("stdout") or "") + (("\n" + data["stderr"]) if data.get("stderr") else "")
    if len(out) > max_bytes:
        out = out[:max_bytes] + "\n[librarian] output truncated"
    return int(data.get("exit_code") if data.get("exit_code") is not None else 1), out
