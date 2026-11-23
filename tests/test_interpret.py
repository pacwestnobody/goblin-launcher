"""Terminal sessions become card candidates: the interpreter reads a Claude/Grok-style trace for a kind, a
dead end and a verified finish, the runtime queues it, and the real writer + checker store the card.
Scripted writer model; no network."""
import json
from pathlib import Path

import yaml

from goblin.librarian import config, interpret
from goblin.librarian.runtime import Librarian
from goblin.librarian.store import Store

ERR = "ImportError: no module named fastjson"


def setup(tmp: Path, mode="store"):
    app = tmp / "app"
    app.mkdir()
    home = tmp / "home"
    home.mkdir()
    card = {
        "type": "repair",
        "route": ["Point the app at its own venv in its config", "Re-run the import check"],
        "dead_fork": {"path": "pip install fastjson", "killed_by_step": 5, "observation": "import still fails after pip install"},
        "probes": {
            "applies": {"read": "py_import", "args": [str(app)], "match": {"regex": "no module named fastjson"}},
            "success": {"read": "py_import", "args": [str(app)], "match": {"regex": "^OK$"}},
        },
    }
    w = tmp / "writer.json"
    w.write_text(json.dumps([{"text": json.dumps(card)}]))
    data = {
        "mode": mode,
        "models": {"w": {"provider": "mock", "script": str(w)}},
        "roles": {"talker": "w", "writer": "w"},
        "reads": {
            "py_import": {"description": "import check", "cmd": ["cat", "{0}/status"], "target": "local", "exit_meaningful": True},
            "https_page": {"description": "page", "cmd": ["curl", "-skL", "https://{0}/"], "target": "local"},
        },
        "job_kinds": {"python_dep_fix": {"tool_family": "python", "stable": True}},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(data))
    return app, config.load(home)


def session_calls(app):
    """A Claude Code session as the hook records it: Bash calls with their exit codes and output."""
    c = lambda cmd, code, out: ("Bash", {"command": cmd, "description": "x"}, code, out)
    return [
        c(f"cat {app}/status 2>&1", 1, ERR),                          # 1 the check fails
        c("pip install fastjson", 0, "Requirement already satisfied"),  # 2 the tempting path
        c(f"cat {app}/config", 0, "venv=system"),                     # 3
        c("pip install --force-reinstall fastjson", 0, "ok"),         # 4
        c(f"cat {app}/status", 1, ERR),                               # 5 still failing: the dead end
        c(f"sed -i s/system/venv/ {app}/config", 0, ""),              # 6 the real fix
        c(f"echo OK > {app}/status", 0, ""),                          # 7
        c(f"cat {app}/status", 0, "OK"),                              # 8 passes
    ]


def test_match_read_with_placeholders_inside_words():
    reads = {"https_page": {"cmd": ["curl", "-skL", "https://{0}/"], "target": "local"},
             "sandbox_only": {"cmd": ["ls", "{0}"], "target": "sandbox"}}
    assert interpret.match_read("curl -skL https://192.168.1.1/ | head", reads) == ("https_page", ["192.168.1.1"])
    assert interpret.match_read("ls /tmp", reads) is None              # reads for other targets are not ours


def test_terminal_session_to_card(tmp_path):
    app, cfg = setup(tmp_path)
    lib = Librarian(cfg)
    for tool, args, code, out in session_calls(app):
        lib.trace_full({"agent": "claude", "session": "s1", "tool": tool, "args": args, "exit": code, "output": out})
    end = lib.end_full({"agent": "claude", "session": "s1", "status": "done"})
    assert end["kind"] == "python_dep_fix", end
    pend = lib.pending()
    assert len(pend) == 1 and pend[0]["kind"] == "python_dep_fix"
    results = lib.write_pending()
    assert results and results[0]["outcome"] == "written", results
    assert not lib.pending()
    assert len(Store(cfg.path(cfg["store"]["path"])).all_readable()) == 1


def test_no_dead_end_no_card(tmp_path):
    app, cfg = setup(tmp_path)
    calls = session_calls(app)
    straight = [calls[0], calls[5], calls[6], calls[7]]                # failed, fixed, passed: nothing abandoned
    lib = Librarian(cfg)
    for tool, args, code, out in straight:
        lib.trace_full({"agent": "grok", "session": "s2", "tool": tool, "args": args, "exit": code, "output": out})
    assert lib.end_full({"agent": "grok", "session": "s2", "status": "done"})["kind"] is None
    assert not lib.pending()


def test_never_fixed_no_card(tmp_path):
    app, cfg = setup(tmp_path)
    lib = Librarian(cfg)
    for tool, args, code, out in session_calls(app)[:5]:
        lib.trace_full({"agent": "grok", "session": "s3", "tool": tool, "args": args, "exit": code, "output": out})
    assert lib.end_full({"agent": "grok", "session": "s3", "status": "done"})["kind"] is None


def test_shadow_mode_classifies_but_queues_nothing(tmp_path):
    app, cfg = setup(tmp_path, mode="shadow")
    lib = Librarian(cfg)
    for tool, args, code, out in session_calls(app):
        lib.trace_full({"agent": "claude", "session": "s4", "tool": tool, "args": args, "exit": code, "output": out})
    assert lib.end_full({"agent": "claude", "session": "s4", "status": "done"})["kind"] == "python_dep_fix"
    assert not lib.pending()


def test_trace_keeps_its_job_kind(tmp_path):
    """The job kind used to overwrite the meta line's marker, so a saved trace lost it on reading back."""
    from goblin.librarian.trace import Trace
    p = tmp_path / "t.jsonl"
    t = Trace(str(p), "j1")
    t.meta["kind"] = "python_dep_fix"
    t.call("Bash", {}, 0, "x")
    t.save()
    u = Trace.load(str(p))
    assert u.meta["kind"] == "python_dep_fix" and len(u.records) == 1
    p.write_text('{"kind": "service_start_fix", "job_id": "old"}\n{"kind": "call", "step": 1, "tool": "x", "exit": 0, "output": ""}\n')
    v = Trace.load(str(p))                                      # files saved the old way still read right
    assert v.meta["kind"] == "service_start_fix" and v.job_id == "old" and len(v.records) == 1
