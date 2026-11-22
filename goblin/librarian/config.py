"""Config loading, API key storage, and model registration.

Settings:  ~/.config/goblin/librarian.yaml   models, roles, targets, reads, tools, job kinds
           ~/.config/goblin/secrets.yaml     API keys only (mode 600), referenced by key_ref
           ~/.config/goblin/shelf.yaml       probed map facts (optional)
Data:      <goblin state>/librarian/         store.db (cards + metrics), traces/ (one JSONL per job)
GOBLIN_LIBRARIAN_HOME puts everything in one folder instead (tests do this).
"""
import copy
import os
from pathlib import Path

import yaml

DEFAULTS = {
    "mode": "shadow",  # shadow | store | live
    "store": {
        "path": "store.db",
        "cap_bytes": 2_000_000,
        "superseded_delay_s": 3600,
        "couldnt_run_limit": 3,
    },
    "consult": {
        "wide_tree_median": 6,   # consult only for kinds whose median trace length is >= this
        "min_history": 3,        # jobs of a kind needed before a median counts
        "mid_walk_step": 5,      # consult mid-walk once a live job passes this many steps
    },
    "write": {
        "min_steps": 6,
        "max_field_chars": 400,
        "max_route_steps": 12,
    },
    "holdout_rate": 0.0,
    "max_steps": 40,
    "tool_output_chars": 8000,
    "roles": {"talker": None, "writer": None},
    "models": {},
    "targets": {"local": {"type": "local"}},
    "reads": {},
    "command_tools": {},
    "mcp_servers": {},
    "builtin_tools": {
        "enabled": True,
        "target": "local",
        "confirm": True,   # ask before shell/write_file run; --yes overrides
        "timeout": 120,
    },
    "job_kinds": {},
    "trace_dir": "traces",
    "shelf": "shelf.yaml",
}


def _goblin():
    from .. import config as goblin_config
    return goblin_config


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """A dict with the resolved home path and helpers attached."""

    def __init__(self, data, root: Path, settings: Path | None = None, goblin=None):
        super().__init__(data)
        self.root = root                      # data: store, traces
        self.settings = settings or root      # librarian.yaml, secrets.yaml, shelf.yaml
        self.goblin = goblin                  # the main Goblin config (shared providers, vault)

    def path(self, rel) -> Path:
        p = Path(rel).expanduser()
        if p.is_absolute():
            return p
        return (self.settings / p) if str(rel).endswith((".yaml", ".yml")) else self.root / p

    # ---- secrets ------------------------------------------------------
    def secrets(self) -> dict:
        p = self.settings / "secrets.yaml"
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text()) or {}

    def resolve_key(self, model_cfg) -> str:
        if model_cfg.get("key_ref"):
            return str(self.secrets().get(model_cfg["key_ref"], ""))
        if model_cfg.get("api_key_env"):
            return os.environ.get(model_cfg["api_key_env"], "")
        return ""

    def model(self, name):
        if name not in self["models"]:
            raise KeyError(f"model '{name}' is not in librarian.yaml")
        return self["models"][name]

    def role_model(self, role):
        name = self["roles"].get(role)
        if not name:
            raise KeyError(f"no model assigned to role '{role}' (set roles.{role} in config.yaml)")
        return name, self.model(name)


def load(root: Path = None, goblin=None) -> Config:
    """root given (or GOBLIN_LIBRARIAN_HOME): one self-contained folder for settings and data.
    Otherwise settings come from ~/.config/goblin and data goes to <goblin state>/librarian."""
    root = root or os.environ.get("GOBLIN_LIBRARIAN_HOME")
    if root:
        root = settings = Path(root).expanduser()
    else:
        goblin = goblin or _goblin().load()
        root, settings = goblin.state / "librarian", _goblin().CONFIG_DIR
    root.mkdir(parents=True, exist_ok=True)
    names = ("librarian.yaml", "config.yaml") if settings == root else ("librarian.yaml",)
    p = next((settings / n for n in names if (settings / n).exists()), None)
    user = (yaml.safe_load(p.read_text()) or {}) if p else {}
    cfg = Config(_merge(DEFAULTS, user), root, settings, goblin)
    cfg.path(cfg["trace_dir"]).mkdir(parents=True, exist_ok=True)
    return cfg


def _write_yaml(path: Path, data, private=False):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    if private:
        os.chmod(tmp, 0o600)
    tmp.replace(path)


def add_model(cfg: Config, name, provider, base_url, model, api_key=None,
              max_tokens=4096, temperature=0.2, role=None):
    """Register a model. The key goes to secrets.yaml, never librarian.yaml."""
    root = cfg.settings
    cpath = root / "librarian.yaml"
    data = yaml.safe_load(cpath.read_text()) if cpath.exists() else {}
    data = data or {}
    entry = {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    if api_key:
        spath = root / "secrets.yaml"
        secrets = yaml.safe_load(spath.read_text()) if spath.exists() else {}
        secrets = secrets or {}
        secrets[name] = api_key
        _write_yaml(spath, secrets, private=True)
        entry["key_ref"] = name
    data.setdefault("models", {})[name] = entry
    if role:
        for r in role.split(","):
            data.setdefault("roles", {})[r.strip()] = name
    _write_yaml(cpath, data)
    return entry
