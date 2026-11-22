"""The phone listener against a real test vault (plain HTTP in the test; TLS checked separately)."""
import hashlib
import json
import ssl
import threading
import urllib.error
import urllib.request

import pytest

from goblin import config, phone
from goblin.vault import server
from goblin.vault.service import VaultService

PIN, PHONE_PIN = "246810", "4321"


@pytest.fixture
def setup(tmp_path):
    v = VaultService(tmp_path / "vh", {"usb_host_dir": str(tmp_path / "usb")})
    sock = tmp_path / "v.sock"
    vs = server.UnixServer(str(sock), server.make_handler(v))
    threading.Thread(target=vs.serve_forever, daemon=True).start()
    v.init(PIN)
    did, priv, pub = v.devices.new_key()
    v.devices.register(did, priv, pub, label="stick", iid=v.iid)
    token = v.pair_phone(did, PHONE_PIN)["token"]
    def make(allow):
        cfg = config.load(tmp_path / "none.yaml", {"paths": {"state": str(tmp_path / "st")}, "vault": {"socket": str(sock)},
                                                   "phone": {"allow": allow}})
        srv = phone.ThreadingHTTPServer(("127.0.0.1", 0), phone.make_handler(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return cfg, f"http://127.0.0.1:{srv.server_address[1]}", srv
    yield v, token, make
    vs.shutdown()


def req(base, method, path, token="", body=None):
    r = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_phone_routes_only(setup):
    v, token, make = setup
    _, base, srv = make(["127.0.0.0/8"])
    assert req(base, "GET", "/health")[0] == 200
    assert req(base, "GET", "/status")[0] == 401                       # token needed
    code, st = req(base, "GET", "/status", token)
    assert code == 200 and st["unlocked"]
    assert req(base, "GET", "/approvals", token)[0] == 200
    assert req(base, "POST", "/phone/check", token, {})[1]["bound"]
    assert req(base, "POST", "/lock", token, {})[0] == 200
    assert req(base, "POST", "/phone/unlock", token, {"phone_pin": "0000"})[0] == 403
    assert req(base, "POST", "/phone/unlock", token, {"phone_pin": PHONE_PIN})[1]["unlocked"]
    for path in ("/run", "/rules", "/pin", "/phones/pair", "/backup", "/devices/enroll-usb"):
        assert req(base, "POST", path, token, {})[0] == 404, path      # never forwarded
    assert req(base, "GET", "/log", token)[0] == 404
    srv.shutdown()


def test_source_networks_enforced(setup):
    _, token, make = setup
    _, base, srv = make(["10.8.0.0/24"])
    assert req(base, "GET", "/status", token)[0] == 403
    srv.shutdown()


def test_certificate_pin_matches_what_is_served(setup):
    _, _, make = setup
    cfg, _, srv = make(["127.0.0.0/8"])
    srv.shutdown()
    cfg["phone"].update(bind="127.0.0.1", port=0)
    tls = phone.start(cfg)
    port = tls.server_address[1]
    der = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(("127.0.0.1", port)))
    assert hashlib.sha256(der).hexdigest() == phone.cert_sha256(cfg)
    tls.shutdown()
