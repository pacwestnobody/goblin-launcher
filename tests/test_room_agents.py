"""Providers, the seat tool loop, the room and the bridge, against fake model servers.
No real model is ever loaded or called, and nothing touches the live room."""
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from goblin import config
from goblin.agents import hook, mcp
from goblin.bridge import Wake
from goblin.providers import make
from goblin.room import http as room_http
from goblin.room.core import Room
from goblin.room.seats import addressed


class FakeModel:
    """Speaks OpenAI /chat/completions, Anthropic /v1/messages and Ollama /api/chat.
    First call asks for list_dir, the next answers with what the tool returned."""

    def __init__(self):
        self.requests = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append((self.path, body))
                msgs = body.get("messages") or []
                had_tool = any(m.get("role") == "tool" or (isinstance(m.get("content"), list)) for m in msgs)
                wants_tools = bool(body.get("tools"))
                if self.path.endswith("/chat/completions"):
                    if wants_tools and not had_tool:
                        out = {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                               "function": {"name": "list_dir", "arguments": json.dumps({"path": "/"})}}]}}]}
                    else:
                        out = {"choices": [{"message": {"content": "answer after tools" if had_tool else "plain answer"}}]}
                elif self.path.endswith("/v1/messages"):
                    if wants_tools and not had_tool:
                        out = {"content": [{"type": "tool_use", "id": "t1", "name": "list_dir", "input": {"path": "/"}}]}
                    else:
                        out = {"content": [{"type": "text", "text": "claude answer"}]}
                else:  # ollama
                    if wants_tools and not had_tool:
                        out = {"message": {"content": "", "tool_calls": [{"function": {"name": "list_dir",
                                                                                         "arguments": {"path": "/"}}}]}}
                    else:
                        out = {"message": {"content": "local answer"}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802  /api/version, /api/ps
                data = json.dumps({"version": "x", "models": []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")
    f = FakeModel()
    yield f
    f.srv.shutdown()


@pytest.fixture
def cfg(tmp_path, fake):
    return config.load(tmp_path / "none.yaml", {
        "owner": "Alex", "machine": "testbox",
        "paths": {"state": str(tmp_path / "state"), "knowledge": str(tmp_path / "kb")},
        "room": {"port": 0},
        "vault": {"socket": str(tmp_path / "no.sock"), "token_file": str(tmp_path / "tok")},
        "gpu": {"provider": "local", "exclusive": True},
        "providers": {"oai": {"type": "openai", "base_url": fake.url, "key_env": "FAKE_KEY"},
                      "ant": {"type": "anthropic", "base_url": fake.url, "key_env": "FAKE_KEY"},
                      "local": {"type": "ollama", "base_url": fake.url}},
        "seats": {"gpt": {"label": "GPT", "provider": "oai", "model": "m"},
                  "claude": {"label": "Claude", "provider": "ant", "model": "m", "aliases": ["claude"]},
                  "sage": {"label": "Sage", "provider": "local", "model": "sage", "guard": True},
                  "sage-tutor": {"label": "Sage tutor", "provider": "local", "model": "sage-tutor",
                                   "aliases": ["sage tutor"]}},
        "bridge": {"room_id": "test-bridge", "authors": {"admin-bot": {"label": "Admin bot"}, "grok-build": {},
                                                          "claude-code": {}},
                   "wake": {"authors": ["admin-bot"], "max_hops": 2}},
    })


def test_every_provider_runs_the_tool_loop(cfg):
    from goblin.agents.loop import run_turn
    for seat in ("gpt", "claude", "sage"):
        s = {"id": seat, **cfg["seats"][seat]}
        out = run_turn(cfg, s, [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}])
        assert out in ("answer after tools", "claude answer", "local answer"), (seat, out)


def test_provider_keys_never_come_from_config_text(cfg, fake, monkeypatch):
    monkeypatch.delenv("FAKE_KEY")
    ok, why = make("oai", cfg["providers"]["oai"]).ready()
    assert not ok and "FAKE_KEY" in why


def test_addressing_prefers_longer_names(cfg):
    seats = {sid: {"id": sid, **s} for sid, s in cfg["seats"].items()}
    ids = list(seats)
    assert addressed("sage tutor, check this", ids, seats) == ["sage-tutor"]
    assert addressed("Claude then sage please", ids, seats) == ["claude", "sage"]
    assert addressed("anyone?", ids, seats) == []


def test_room_turns_and_cross_talk(cfg):
    room = Room(cfg)
    r = room.create("brief", ["gpt", "claude"])
    room.say(r["id"], "claude, look at /")
    for _ in range(100):
        msgs = room.rooms.get(r["id"])["messages"]
        if len(msgs) >= 2 and room.rooms.get(r["id"])["status"] == "idle":
            break
        time.sleep(0.05)
    msgs = room.rooms.get(r["id"])["messages"]
    assert [m["author_id"] for m in msgs] == ["you", "claude"]            # only the named seat answered
    seats = {sid: {"id": sid, **s} for sid, s in cfg["seats"].items()}
    conv = room.conversation(seats["gpt"], room.rooms.get(r["id"]), seats)
    assert conv[-1]["content"].startswith("[Claude said]:")              # gpt sees Claude labelled


def test_guarded_seat_cant_grep_the_notes(cfg):
    from goblin.agents.tools import Tools
    t = Tools(cfg, {"id": "sage", "guard": True})
    assert "librarian_lookup" in t.run("run_terminal_command", {"command": f"grep -r vmbr {cfg.path('paths', 'knowledge')}"})
    assert t.run("run_terminal_command", {"command": "echo hi"}).startswith("exit=0")
    assert "blocked" in t.run("run_terminal_command", {"command": "sudo reboot"})


def test_bridge_http_api_unchanged(cfg):
    room = Room(cfg)
    srv = room_http.ThreadingHTTPServer(("127.0.0.1", 0), room_http.make_handler(room))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def req(method, path, body=None, headers=None):
        r = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    code, out = req("POST", "/api/bridge/message", {"author_id": "admin-bot", "text": "hi", "client_nonce": "n1"})
    assert code == 200 and out["count"] == 1
    assert req("POST", "/api/bridge/message", {"author_id": "admin-bot", "text": "hi", "client_nonce": "n1"})[1]["duplicate"]
    assert req("POST", "/api/bridge/message", {"author_id": "stranger", "text": "x"})[0] == 400
    assert req("POST", "/api/bridge/message", {"author_id": "admin-bot", "text": "x"},
               {"Origin": "https://evil.example"})[0] == 403
    first = out["message"]["id"]
    threading.Timer(0.3, lambda: room.bridge_post("grok-build", "on it")).start()
    code, view = req("GET", f"/api/bridge/wait?after_id={first}&timeout_s=5")
    assert [m["text"] for m in view["messages"]] == ["on it"] and not view["timed_out"]
    assert req("GET", "/api/bridge?after_id=nonexistent")[1]["resync"]
    srv.shutdown()


def test_bridge_wake_routing(cfg):
    w = Wake(cfg)
    assert w.route({"author_id": "admin-bot", "text": "reboot nothing please"}) == ["grok"]
    assert w.route({"author_id": "admin-bot", "text": "claude, check the logs"}) == ["claude"]
    assert w.route({"author_id": "admin-bot", "text": "grok and claude both"}) == ["grok", "claude"]
    assert w.route({"author_id": "grok-build", "text": "done. @claude can you review?"}) == ["claude"]
    assert w.route({"author_id": "claude-code", "text": "@grok looks good"}) == ["grok"]
    assert w.route({"author_id": "grok-build", "text": "@claude again"}) == []          # max_hops 2 reached
    assert w.route({"author_id": "grok-build", "text": "claude is mentioned but not addressed"}) == []


def test_hook_is_inert_outside_launches(monkeypatch, capsys):
    monkeypatch.delenv("GOBLIN_LAUNCH", raising=False)
    assert hook.main(["claude"]) == 0 and capsys.readouterr().out == ""


def test_mcp_lists_tools_without_a_vault(cfg):
    out = mcp.handle(cfg, {"method": "tools/list", "id": 1}, "test", "s")
    assert [t["name"] for t in out["tools"]] == ["librarian_context", "lab_run", "librarian_shelf"]
    assert mcp.handle(cfg, {"method": "notifications/initialized"}, "test", "s") is None


def test_room_list_ignores_other_json(cfg):
    room = Room(cfg)
    d = room.rooms.dir
    (d / "20261122-000000-abcdef.cli-sessions.json").write_text('{"grok-cli": "uuid"}')
    (d / "notaroom.json").write_text('{"just": "data"}')
    (d / "home-bridge.json.empty-20261122").write_text("")
    ids = [r["id"] for r in room.rooms.listing()]
    assert ids == [room.bridge_id]
