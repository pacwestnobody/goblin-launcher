"""The launcher page's server, end to end on spare ports (no vault, no models, no live room)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from goblin import config
from goblin.room import http as room_http
from goblin.service import Service, socket_handler, UnixServer
from goblin.ui import server as ui_server


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBLIN_CONFIG_DIR", str(tmp_path / "cfgdir"))
    (tmp_path / "cfgdir").mkdir()
    (tmp_path / "cfgdir" / "librarian.yaml").write_text("mode: shadow\nmodels: {}\n")
    import goblin.config as gc
    monkeypatch.setattr(gc, "CONFIG_DIR", tmp_path / "cfgdir")
    cfg = config.load(tmp_path / "none.yaml", {
        "owner": "Alex", "machine": "testbox", "tagline": "built it",
        "paths": {"state": str(tmp_path / "state"), "knowledge": str(tmp_path / "kb")},
        "vault": {"socket": str(tmp_path / "no-vault.sock"), "token_file": str(tmp_path / "tok")},
        "room": {"port": 0}, "ui": {"port": 0},
        "providers": {"local": {"type": "ollama", "base_url": "http://127.0.0.1:9"}},
        "gpu": {"provider": "local"},
        "dials": {"furnace": [{"slot": "sage", "name": "Sage", "model": "sage", "seat": "sage"}], "aether": []},
        "seats": {"sage": {"label": "Sage", "provider": "local", "model": "sage"}},
        "bridge": {"room_id": "test-bridge", "authors": {"bot": {"label": "Bot"}}},
    })
    svc = Service(cfg)
    room = room_http.ThreadingHTTPServer(("127.0.0.1", 0), room_http.make_handler(svc.room))
    threading.Thread(target=room.serve_forever, daemon=True).start()
    cfg["room"]["port"] = room.server_address[1]
    ui = ui_server.ThreadingHTTPServer(("127.0.0.1", 0), ui_server.make_handler(ui_server.Api(svc)))
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    sock = tmp_path / "g.sock"
    us = UnixServer(str(sock), socket_handler(svc))
    threading.Thread(target=us.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{ui.server_address[1]}", svc, sock
    for s in (room, ui, us):
        s.shutdown()


def call(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_page_and_static_files(launcher):
    base, _, _ = launcher
    code, html = call(base, "GET", "/")
    assert code == 200 and b"/static/goblin.js" in html and b"/static/ledger.js" in html
    for f in ("goblin.css", "goblin.js", "ledger.js", "img/goblin.webp", "fonts/fonts.css"):
        assert call(base, "GET", f"/static/{f}")[0] == 200, f
    assert call(base, "GET", "/static/../server.py")[0] == 404


def test_dials_brand_and_furnace_allowlist(launcher):
    base, _, _ = launcher
    code, d = call(base, "GET", "/api/dials")
    assert code == 200 and d["brand"] == "testbox · built it" and d["furnace"][0]["model"] == "sage"
    assert call(base, "POST", "/api/furnace/load", {"model": "something-else"})[0] == 400


def test_vault_down_is_reported_not_fatal(launcher):
    base, _, _ = launcher
    code, v = call(base, "GET", "/api/vault/status")
    assert code == 200 and v["reachable"] is False
    assert call(base, "POST", "/api/vault/unlock", {"pin": "123456"})[0] == 502


def test_room_through_the_launcher(launcher):
    base, _, _ = launcher
    code, st = call(base, "GET", "/room/api/status")
    assert code == 200 and st["seats"][0]["id"] == "sage"
    code, r = call(base, "GET", "/room/api/rooms")
    assert any(x["bridge"] for x in r["rooms"])


def test_cross_site_posts_refused(launcher):
    base, _, _ = launcher
    assert call(base, "POST", "/api/stack/unload", {}, {"Origin": "https://evil.example"})[0] == 403


def test_ledger_and_shelf(launcher):
    base, _, _ = launcher
    code, st = call(base, "GET", "/api/ledger/status")
    assert code == 200 and st["mode"] == "shadow" and st["cards"] == 0
    assert call(base, "GET", "/api/ledger/cards") == (200, [])
    assert "mode: shadow" in call(base, "GET", "/api/ledger/report")[1]["text"]
    assert call(base, "POST", "/api/ledger/jobs", {"task": ""})[0] == 400
    assert call(base, "GET", "/api/shelf")[0] == 200


def test_agent_socket_context_and_trace(launcher):
    _, svc, sock = launcher
    from goblin.sockclient import call as scall
    r = scall("POST", "/v1/context", {"prompt": "hello", "agent": "claude", "session": "s1", "knowledge": False}, sock=sock)
    assert r is not None and "text" in r
    r = scall("POST", "/v1/trace", {"agent": "claude", "session": "s1", "tool": "Bash", "args": {"command": "ls"},
                                    "exit": 0, "output": "token=sk-abcdefghijklmnopqrstuvwx"}, sock=sock)
    assert r["ok"]
    assert scall("POST", "/v1/end", {"agent": "claude", "session": "s1", "status": "done"}, sock=sock)["saved"]
    assert scall("GET", "/v1/health", sock=sock)["ok"]
