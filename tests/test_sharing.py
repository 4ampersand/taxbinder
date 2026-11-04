"""Shared access on the home network: pairing, devices, certificate, the HTTPS listener."""

from __future__ import annotations

import datetime as dt
import http.client
import ssl
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_web import BASE, KETTLE, ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder.web import sharing as lan
from taxbinder.web.app import AppState, create_app

LAN = "https://testmac.local:8765"
LAN_ORIGIN = {"Origin": LAN}


@pytest.fixture
def shared(state: AppState) -> AppState:  # noqa: F811
    """Sharing turned on, without starting a real listener (TestClient talks to the app)."""
    state.sharing = lan.Sharing(port=8765, host="testmac", ip="192.168.1.20", fingerprint="AA:BB")
    return state


def local(state: AppState) -> TestClient:  # noqa: F811
    """The app window on this Mac (its login code is one-time, so one client per test)."""
    if not hasattr(state, "local_client"):
        state.local_client = client(state)  # type: ignore[attr-defined]
    return state.local_client  # type: ignore[attr-defined]


def remote(state: AppState) -> TestClient:  # noqa: F811
    """A browser on another device: requests arrive through the network listener."""
    return TestClient(lan.lan_app(create_app(state)), base_url=LAN)


def pair(state: AppState, name: str = "Kitchen iPad") -> TestClient:  # noqa: F811
    code = local(state).post("/api/pairing", headers=ORIGIN).json()["code"]
    r = remote(state)
    resp = r.post("/api/pair", json={"code": code, "name": name}, headers=LAN_ORIGIN)
    assert resp.status_code == 200, resp.text
    return r


# -- units --------------------------------------------------------------------------------


def test_pairing_code_is_single_use_and_limited() -> None:
    p = lan.Pairing.new()
    assert len(p.code) == 6 and p.code.isdigit()
    wrong = "000000" if p.code != "000000" else "111111"
    assert p.try_code(wrong) == "wrong" and p.tries_left == 4
    assert p.try_code(f"{p.code[:3]} {p.code[3:]}") == "ok"  # spaces are fine
    assert p.try_code(p.code) == "closed"  # single use

    q = lan.Pairing.new()
    for _ in range(4):
        assert q.try_code(wrong) == "wrong"
    assert q.try_code(wrong) == "closed" and q.try_code(q.code) == "closed"  # locked after 5

    old = lan.Pairing("123456", lan.now() - dt.timedelta(seconds=1))
    assert old.try_code("123456") == "closed"


def test_devices_store_hashes_only(tmp_path: Path) -> None:
    path = tmp_path / "private" / "devices.json"
    devices = lan.Devices.load(path)
    device, token = devices.add("Kitchen iPad")
    assert token not in path.read_text() and stat.S_IMODE(path.stat().st_mode) == 0o600
    again = lan.Devices.load(path)
    assert again.check(token).id == device.id and again.check("nope") is None
    assert again.revoke(device.id) and lan.Devices.load(path).check(token) is None


def test_certificate_is_reused_and_names_this_mac(tmp_path: Path) -> None:
    from cryptography import x509

    cert, key, fp = lan.ensure_certificate(tmp_path, "testmac", "192.168.1.20")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and len(fp.split(":")) == 32
    san = x509.load_pem_x509_certificate(cert.read_bytes()).extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    )
    assert san.value.get_values_for_type(x509.DNSName) == ["testmac.local"]
    assert lan.ensure_certificate(tmp_path, "testmac", "192.168.1.20")[2] == fp  # reused
    assert lan.ensure_certificate(tmp_path, "testmac", "192.168.1.99")[2] != fp  # IP changed


# -- the app over the network -------------------------------------------------------------


def test_network_is_closed_until_sharing_is_on(state: AppState) -> None:  # noqa: F811
    assert remote(state).get("/pair").status_code == 503


def test_unpaired_devices_only_see_pairing(shared: AppState) -> None:
    r = remote(shared)
    assert r.get("/", follow_redirects=False).headers["location"] == "/pair"
    assert r.get("/api/status").status_code == 401
    assert "Pair this device" in r.get("/pair").text
    assert r.get("/pair", headers={"Host": "evil.example:8765"}).status_code == 421
    assert (
        r.post("/api/pair", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    )
    no_code = r.post("/api/pair", json={"code": "123456"}, headers=LAN_ORIGIN)
    assert no_code.status_code == 400 and "no pairing is open" in no_code.json()["error"]


def test_pairing_flow_and_device_cookie(shared: AppState) -> None:
    c = local(shared)
    code = c.post("/api/pairing", headers=ORIGIN).json()["code"]
    assert code[:3] in c.get("/sharing").text
    r = remote(shared)
    bad = r.post("/api/pair", json={"code": "x", "name": "P"}, headers=LAN_ORIGIN)
    assert bad.status_code == 400 and "4 tries left" in bad.json()["error"]
    ok = r.post("/api/pair", json={"code": code, "name": "Kitchen iPad"}, headers=LAN_ORIGIN)
    cookie = ok.headers["set-cookie"].lower()
    assert ok.status_code == 200 and all(
        f in cookie for f in ("httponly", "secure", "samesite=strict")
    )
    again = remote(shared).post("/api/pair", json={"code": code, "name": "X"}, headers=LAN_ORIGIN)
    assert again.status_code == 400  # used up

    html = r.get("/").text
    assert 'class="deck"' in html and "on Kitchen iPad" in html
    assert "Strict-Transport-Security" in r.get("/").headers
    assert "Get documents from a portal" not in r.get("/documents").text  # drives the Mac
    assert 'id="fx-pick-folder"' not in r.get("/add").text  # a phone has no folders
    assert "Kitchen iPad" in c.get("/sharing").text


def test_paired_device_cannot_drive_this_mac(shared: AppState) -> None:
    r = pair(shared)
    for path in ("/api/open-portal", "/api/capture-page", "/api/pairing", "/api/sharing"):
        assert r.post(path, json={}, headers=LAN_ORIGIN).status_code == 403, path
    assert r.get("/sharing").status_code == 403
    for path in ("/api/redact", "/api/redact/save", "/api/unredact", "/api/privacy", "/api/backup"):
        assert r.post(path, json={}, headers=LAN_ORIGIN).status_code == 403, path
    assert r.get("/privacy").status_code == 403
    assert r.get("/backup").status_code == 403
    assert r.get("/bootstrap?t=x").status_code == 403


def test_writes_are_attributed_to_the_device(shared: AppState) -> None:
    r = pair(shared)
    assert r.post("/api/verify", json={"id": KETTLE}, headers=LAN_ORIGIN).status_code == 200
    author, message = shared.store.vault.history.log()[1]  # newest is the reconcile after it
    assert author.startswith("app:kitchen-ipad <") and message.startswith("verify ")


def test_revoked_device_is_locked_out(shared: AppState) -> None:
    r = pair(shared)
    device = shared.devices.items[0]
    c = local(shared)
    assert c.post("/api/devices/revoke", json={"id": device.id}, headers=ORIGIN).status_code == 200
    assert r.get("/api/status").status_code == 401


def test_real_https_listener(state: AppState) -> None:  # noqa: F811
    """Turn sharing on for real (any free port), talk TLS to it, turn it off."""
    app = create_app(state)
    sh = lan.start(app, state.vault_path / "private", port=0)
    state.sharing = sh
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # self-signed, by design
        conn = http.client.HTTPSConnection("127.0.0.1", sh.port, context=ctx, timeout=5)
        conn.request("GET", "/healthz", headers={"Host": f"{sh.host}.local:{sh.port}"})
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
        conn.request("GET", "/", headers={"Host": f"{sh.host}.local:{sh.port}"})
        resp = conn.getresponse()
        assert resp.status == 303 and resp.getheader("location") == "/pair"
        resp.read()
        conn.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{sh.port}"})
        assert conn.getresponse().status == 421  # loopback name on the LAN port
        conn.close()
        v6 = http.client.HTTPSConnection("::1", sh.port, context=ctx, timeout=5)  # `.local` → IPv6
        v6.request("GET", "/healthz", headers={"Host": f"{sh.host}.local:{sh.port}"})
        assert v6.getresponse().status == 200
        v6.close()
        plain = http.client.HTTPConnection("127.0.0.1", sh.port, timeout=5)
        with pytest.raises((http.client.HTTPException, ConnectionError, OSError)):
            plain.request("GET", "/healthz")
            plain.getresponse()  # HTTPS only
    finally:
        sh.stop()
        state.sharing = None


def test_loopback_unchanged_while_shared(shared: AppState) -> None:
    c = local(shared)
    assert c.get("/").status_code == 200 and "Shared · 8765" in c.get("/").text
    assert TestClient(create_app(shared), base_url=BASE).get("/api/status").status_code == 401


def test_paired_device_downloads_in_the_browser(shared: AppState) -> None:
    from pdfgen import make_pdf
    from test_web import _upload

    k1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    doc_id = _upload(local(shared), k1, "k1.pdf").json()["doc_id"]
    r = pair(shared)
    assert 'data-remote="true"' in r.get("/documents").text
    got = r.get(f"/documents/{doc_id}/file")
    assert got.status_code == 200 and got.content == k1
    assert r.get("/documents/zip?year=2025").status_code == 200
    saved = r.post("/api/documents/save", json={"ids": [doc_id]}, headers=LAN_ORIGIN)
    assert saved.status_code == 403  # that would write into this Mac's Downloads
    stranger = remote(shared).get(f"/documents/{doc_id}/file", follow_redirects=False)
    assert stranger.status_code == 303 and stranger.headers["location"] == "/pair"
    assert k1 not in stranger.content
    assert (
        remote(shared).get(f"/documents/zip?ids={doc_id}", follow_redirects=False).status_code
        == 303
    )
