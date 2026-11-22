"""Tool registry for the talker.

Sources, all optional and combinable:
  builtin   shell, read_file, write_file, list_dir on a chosen target
            (the same basic set a CLI agent harness gives a model)
  reads     every allowlisted read becomes read_<id>. Observations made
            this way can later become probes, so the talker is told to
            prefer them for anything it checks.
  command   your own CLIs wrapped as tools (argv templates, no shell)
  mcp       any MCP stdio server you already run
  control   abandon_fork, finish, propose_shelf_entry (handled by the agent)
"""
import re

from . import probes, targets
from .mcp_client import MCPServer, MCPError

CONTROL_TOOLS = [
    {
        "name": "abandon_fork",
        "description": ("Call this when you give up on a path that looked right. at_step is the step "
                        "number of the observation that killed it. Use a read_* tool for that "
                        "observation when you can, so it can be re-checked later."),
        "schema": {"type": "object", "properties": {
            "at_step": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["at_step", "reason"]},
    },
    {
        "name": "finish",
        "description": ("End the job. status is success or failed. evidence_step is the step number of "
                        "the check that shows the result (a read_* step if possible)."),
        "schema": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["success", "failed"]},
            "evidence_step": {"type": "integer"},
            "summary": {"type": "string"}},
            "required": ["status", "summary"]},
    },
    {
        "name": "propose_shelf_entry",
        "description": ("Propose a fact for the map shelf (what runs where, which address). It is only "
                        "saved if its probe passes right now. probe = {read, args, match}."),
        "schema": {"type": "object", "properties": {
            "id": {"type": "string"}, "fact": {"type": "string"},
            "probe": {"type": "object"}},
            "required": ["id", "fact", "probe"]},
    },
]


class Tool:
    def __init__(self, name, description, schema, fn, confirm=False, family="other", read_id=None):
        self.name, self.description, self.schema = name, description, schema
        self.fn, self.confirm, self.family, self.read_id = fn, confirm, family, read_id

    def public(self):
        return {"name": self.name, "description": self.description, "schema": self.schema}


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", s)[:60]


class ToolRegistry:
    def __init__(self, cfg, with_mcp=True):
        self.cfg = cfg
        self.tools = {}
        self.mcp = {}
        self._builtins()
        self._reads()
        self._commands()
        if with_mcp:
            self._mcp()
        g = getattr(cfg, "goblin", None)
        if g is not None and self.cfg.get("lab_run", True):
            from .. import lab

            def _lab(a):
                return lab.run(g, str(a.get("target") or ""), str(a.get("command") or ""), agent="launcher-job")
            self.tools["lab_run"] = Tool("lab_run", lab.describe(g), lab.PARAMS, _lab, False, "lab")
        for t in CONTROL_TOOLS:
            self.tools[t["name"]] = Tool(t["name"], t["description"], t["schema"], None, family="control")

    # ---- sources --------------------------------------------------------
    def _builtins(self):
        b = self.cfg["builtin_tools"]
        if not b.get("enabled", True):
            return
        tgt, to, conf = b.get("target", "local"), b.get("timeout", 120), b.get("confirm", True)

        def shell(a):
            return targets.run(self.cfg, tgt, ["sh", "-c", a["command"]], timeout=to)

        def read_file(a):
            return targets.run(self.cfg, tgt, ["cat", "--", a["path"]], timeout=to)

        def list_dir(a):
            return targets.run(self.cfg, tgt, ["ls", "-la", "--", a.get("path", ".")], timeout=to)

        def write_file(a):
            return targets.run(self.cfg, tgt, ["sh", "-c", 'cat > "$1"', "sh", a["path"]],
                               timeout=to, stdin=a.get("content", ""))

        s = lambda props, req: {"type": "object", "properties": props, "required": req}
        self.tools["shell"] = Tool("shell", f"Run a shell command on target '{tgt}' (see where_you_are).",
                                   s({"command": {"type": "string"}}, ["command"]), shell, conf, "shell")
        self.tools["read_file"] = Tool("read_file", "Read a text file.",
                                       s({"path": {"type": "string"}}, ["path"]), read_file, False, "fs")
        self.tools["list_dir"] = Tool("list_dir", "List a directory.",
                                      s({"path": {"type": "string"}}, []), list_dir, False, "fs")
        self.tools["write_file"] = Tool("write_file", "Write a whole text file.",
                                        s({"path": {"type": "string"}, "content": {"type": "string"}},
                                          ["path", "content"]), write_file, conf, "fs")

    def _reads(self):
        for rid, spec in self.cfg["reads"].items():
            n = probes._placeholders(spec["cmd"])
            schema = {"type": "object", "properties": {
                "args": {"type": "array", "items": {"type": "string"}, "minItems": n, "maxItems": n,
                         "description": f"exactly {n} argument(s)"}}, "required": ["args"]}
            fn = (lambda r: (lambda a: probes.run_read(self.cfg, r, a.get("args", []))))(rid)
            where = spec.get("target", "local")
            where = self.cfg["builtin_tools"].get("target", "local") if where == "job" else where
            desc = (f"[checkable read, runs on {where}] {spec.get('description', rid)} "
                    f"(cmd: {' '.join(spec['cmd'])})")
            self.tools[f"read_{_safe_name(rid)}"] = Tool(f"read_{_safe_name(rid)}", desc, schema, fn,
                                                         False, spec.get("family", "read"), read_id=rid)

    def _commands(self):
        for name, spec in self.cfg["command_tools"].items():
            params = spec.get("params", {})
            schema = {"type": "object", "properties": params, "required": list(params)}

            def make(spec=spec):
                def fn(a):
                    argv = []
                    for part in spec["cmd"]:
                        argv.append(re.sub(r"\{(\w+)\}", lambda m: str(a.get(m.group(1), "")), part))
                    return targets.run(self.cfg, spec.get("target", "local"), argv,
                                       timeout=spec.get("timeout", 120))
                return fn

            tname = _safe_name(name)
            desc = f"[runs on {spec.get('target', 'local')}] {spec.get('description', name)}"
            self.tools[tname] = Tool(tname, desc, schema, make(),
                                     spec.get("confirm", True), spec.get("family", "command"))

    def _mcp(self):
        for sname, spec in self.cfg["mcp_servers"].items():
            try:
                srv = MCPServer(sname, spec)
                listed = srv.list_tools()
            except (MCPError, OSError) as e:
                print(f"[librarian] MCP server '{sname}' unavailable: {e}")
                continue
            self.mcp[sname] = srv
            for t in listed:
                tname = _safe_name(f"mcp_{sname}__{t['name']}")
                fn = (lambda s, n: (lambda a: s.call(n, a)))(srv, t["name"])
                self.tools[tname] = Tool(tname, t.get("description", t["name"]),
                                         t.get("inputSchema") or {"type": "object", "properties": {}},
                                         fn, spec.get("confirm", False), f"mcp:{sname}")

    # ---- use ------------------------------------------------------------
    def public(self):
        return [t.public() for t in self.tools.values()]

    def call(self, name, args, confirm_cb=None):
        t = self.tools.get(name)
        if t is None:
            return 1, f"[librarian] no tool named '{name}'"
        if t.fn is None:
            return 1, f"[librarian] '{name}' is a control tool"
        blocked = self._denied(t, args)
        if blocked:
            return 1, f"[librarian] denied: {blocked} is off limits"
        if t.confirm and confirm_cb is not None and not confirm_cb(name, args):
            return 1, "[librarian] the user declined this action"
        try:
            code, out = t.fn(args)
        except (MCPError, KeyError, TypeError) as e:
            return targets.COULDNT_RUN, f"[librarian] tool error: {e}"
        return code, out

    def _denied(self, t, args):
        """Soft path deny-list (config deny_paths) for tools that touch this machine's files.
        Not a sandbox; the sandbox target is. Keeps secrets out of traces and context."""
        if t.family not in ("shell", "fs") or self.cfg["builtin_tools"].get("target", "local") != "local":
            return None
        import json as _json
        import os as _os
        text = _json.dumps(args)
        for p in self.cfg.get("deny_paths", []):
            full = _os.path.expanduser(p)
            short = p if p.startswith("~") else None
            if full in text or (short and short in text):
                return p
        return None

    def close(self):
        for s in self.mcp.values():
            s.close()
