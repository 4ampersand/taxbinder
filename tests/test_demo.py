"""Try with sample data: one click from first run to a demo binder set up from
last year's sample return; its sample documents reach Check; "Demo" on every page; it's never
backed up, shared or given a Keychain item; leaving it leaves the person's binder and Keychain
exactly as they were (the invented household; sample files made in the test)."""

from __future__ import annotations

import hashlib
import time
from datetime import date
from pathlib import Path

import pytest
from samples.demo import build as build_samples
from test_add import _wait
from test_start import _state
from test_web import ORIGIN, client

from taxbinder import check, demo
from taxbinder.store import keys as keys_module


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_samples(tmp_path_factory.mktemp("demo-samples"))


@pytest.fixture(autouse=True)
def _bundled(samples: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXBINDER_SAMPLES", str(samples))  # as the app has them, bundled


def _files(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != "index.sqlite" and not p.name.startswith("index.sqlite-")
    }


def _keychain() -> dict[str, str]:
    return dict(keys_module._MEMORY._keys)  # the test stand-in for the Keychain


def test_one_click_from_first_run_and_back(tmp_path: Path) -> None:
    vault = tmp_path / "app" / "vault"
    state = _state(vault, tmp_path)
    assert state.problem_kind == "new"
    c = client(state)
    assert "demo-start" in c.get("/").text  # Try with sample data, on the Start page
    keychain = _keychain()
    r = c.post("/api/demo", json={"action": "start"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"demo": True, "go": "/add", "samples": True}
    store = state.store
    assert state.demo and store is not None
    assert store.vault.root == demo.vault_path(vault) and not vault.exists()
    # Set up from last year's return, as a person would on Check.
    names = {t.name for t in store.list("taxpayer")}
    assert {"Avery Quill", "Jordan Quill"} <= names
    assert len(store.list("expectation", 2025)) >= 10 and not check.waiting(store)
    assert _keychain() == keychain  # no Keychain item: its own key file
    assert (demo.root(vault) / "demo-key.json").exists()
    for path in ("/", "/add", "/check", "/documents", "/settings"):
        html = c.get(path).text
        assert 'class="demo-bar"' in html and "invented household" in html, path
    assert 'href="/backup">Keep it safe' not in c.get("/").text  # nothing to keep safe
    assert state.today() == demo.TODAY == date(2026, 3, 1)  # mid-season, whenever it's opened
    assert "45 days left" in c.get("/").text  # to April 15, never "passed"

    r = c.post("/api/demo", json={"action": "leave"}, headers=ORIGIN)
    assert r.status_code == 200 and not state.demo and state.problem_kind == "new"
    assert "Welcome to TaxBinder" in c.get("/").text and not vault.exists()
    assert _keychain() == keychain
    assert state.today() == date.today()  # yours again


def test_the_sample_documents_reach_check(tmp_path: Path) -> None:
    vault = tmp_path / "app" / "vault"
    state = _state(vault, tmp_path)
    c = client(state)
    assert c.post("/api/start", headers=ORIGIN).status_code == 200  # the person's own binder
    mine = _files(vault.parent)
    keychain = _keychain()
    assert c.post("/api/demo", json={"action": "start"}, headers=ORIGIN).status_code == 200
    assert 'id="fx-demo"' in c.get("/add").text
    r = c.post("/api/demo/add", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["queued"] == len(demo.documents())
    c.app.state.reader.wake()
    st = _wait(c)
    assert st["bad"] >= 1  # the damaged file, reported on its own
    found = check.report(state.store)
    assert found.total >= 20 and "Here's what we found" in c.get("/check").text

    # Kept apart: never backed up, never shared, no websites.
    assert c.post("/api/backup", headers=ORIGIN).status_code == 400
    assert c.post("/api/sharing", json={"on": True}, headers=ORIGIN).status_code == 400
    r = c.post("/api/open-portal", json={"url": "https://example.com"}, headers=ORIGIN)
    assert r.status_code == 400

    # Start over: the demo as new; then leave: the person's binder exactly as it was.
    assert c.post("/api/demo", json={"action": "over"}, headers=ORIGIN).status_code == 200
    assert state.demo and not check.waiting(state.store)
    c.app.state.reader.stop()
    time.sleep(0.1)
    assert c.post("/api/demo", json={"action": "leave"}, headers=ORIGIN).status_code == 200
    status = c.get("/api/add/status").json()  # your Add page: nothing of the demo's
    assert status.get("total", 0) == 0 and "doc-001.pdf" not in c.get("/add").text
    assert state.store.vault.root == vault
    assert {k: v for k, v in _files(vault.parent).items() if not k.startswith("demo/")} == mine
    assert _keychain() == keychain
    state.store.close()


def test_the_demo_is_this_macs_only() -> None:
    from taxbinder.web.app import LOCAL_ONLY

    for path in ("/api/demo", "/api/demo/add"):
        assert path in LOCAL_ONLY
