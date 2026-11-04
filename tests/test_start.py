"""Start: Get started creates the vault with no questions; Unlock puts the key
back from the recovery key; Restore brings a backup into place. All in the app (fictional data)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pdfgen import make_pdf
from pyrage import x25519
from quills import build
from test_web import ORIGIN, PORT, client

from taxbinder import backup, start
from taxbinder.entities import create_entity, list_entities
from taxbinder.ingest.inbox import ingest_file
from taxbinder.store import RecordStore, init_vault, keys_from_env, open_vault
from taxbinder.store import keys as keys_module
from taxbinder.web.app import AppState


def _state(vault: Path, tmp_path: Path) -> AppState:
    s = AppState(vault_path=vault, port=PORT, emit=lambda e: None)
    s.downloads_dir = tmp_path / "downloads"
    s.open()
    return s


def _new_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another Mac (or a reset Keychain): no keys at all."""
    monkeypatch.setattr(keys_module, "_MEMORY", keys_module.MemoryKeys())


def _household(tmp_path: Path) -> tuple[Path, str]:
    """The invented household (with its trust), a document, and an entity; the recovery key."""
    vault, recovery = init_vault(tmp_path / "app" / "vault", keys_from_env())
    with RecordStore(vault) as store:
        build(store, "test")
        pdf = vault.inbox / "k1.pdf"
        pdf.write_bytes(
            make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
        )
        ingest_file(store, pdf, "upload", "test")
    create_entity(vault.root, keys_from_env(), "Northwind Holdings Inc.", "1120", "test")
    return vault.root, recovery


def test_get_started_creates_the_vault_with_no_questions(tmp_path: Path) -> None:
    vault = tmp_path / "app" / "vault"
    (vault / "inbox" / "portal").mkdir(parents=True)  # the app shell makes this before we start
    state = _state(vault, tmp_path)
    assert state.store is None and state.problem_kind == "new"
    c = client(state)
    page = c.get("/documents")
    assert page.status_code == 503 and "Welcome to TaxBinder" in page.text
    assert "taxbinder init" not in page.text and "Terminal" not in page.text
    assert 'href="/start/restore"' in page.text and 'href="/start/unlock"' in page.text
    assert "Last backup" not in page.text and '<select id="year-pick"' not in page.text
    assert 'class="topnav"' in page.text and "topnav-links" not in page.text  # no binder yet

    r = c.post("/api/start", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["go"] == "/add"
    assert state.store is not None and c.get("/documents").status_code == 200
    assert c.post("/api/start", headers=ORIGIN).status_code == 400  # never twice
    assert c.get("/start/restore", follow_redirects=False).status_code == 303
    # The recovery key waits in the Keychain for Keep it safe; it opens the vault.
    recovery = keys_from_env().get(start.pending_recovery_id(state.store.vault))
    assert recovery and recovery.startswith("AGE-SECRET-KEY-")
    state.store.close()
    again = _state(vault, tmp_path)  # after a restart it opens by itself
    assert again.store is not None
    again.store.close()


def test_unlock_with_the_recovery_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, recovery = _household(tmp_path)
    _new_mac(monkeypatch)
    state = _state(vault, tmp_path)
    assert state.problem_kind == "nokey"
    c = client(state)
    page = c.get("/")
    assert "Unlock your binder" in page.text and 'id="unlock-key"' in page.text
    assert "restore-key" not in page.text  # no Terminal command
    bad = c.post("/api/start/unlock", json={"key": str(x25519.Identity.generate())}, headers=ORIGIN)
    assert bad.status_code == 400 and "Nothing was changed" in bad.json()["error"]
    assert c.post("/api/start/unlock", json={"key": "nonsense"}, headers=ORIGIN).status_code == 400
    assert state.store is None
    r = c.post("/api/start/unlock", json={"key": recovery}, headers=ORIGIN)
    assert r.status_code == 200 and state.store is not None
    assert len(state.store.list("document")) == 1
    for ref in list_entities(vault):  # every filer's key is back
        open_vault(ref.path, keys_from_env())
    state.store.close()


def test_a_recovery_key_alone_brings_nothing_back(tmp_path: Path) -> None:
    state = _state(tmp_path / "app" / "vault", tmp_path)
    c = client(state)
    page = c.get("/start/unlock").text
    assert "can't bring documents back" in page and 'id="unlock-go" disabled' in page
    r = c.post("/api/start/unlock", json={"key": str(x25519.Identity.generate())}, headers=ORIGIN)
    assert r.status_code == 400 and "restore a backup" in r.json()["error"]
    assert not (tmp_path / "app" / "vault" / "records").exists()


def test_restore_a_backup_on_a_new_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, recovery = _household(tmp_path)
    made = backup.create(open_vault(vault, keys_from_env()), tmp_path / "out")
    data = made.path.read_bytes()
    _new_mac(monkeypatch)
    home = tmp_path / "new-mac" / "vault"
    (home / "inbox" / "portal").mkdir(parents=True)
    state = _state(home, tmp_path)
    c = client(state)
    assert 'id="restore-file"' in c.get("/start/restore").text

    def restore(body: bytes, key: str):  # noqa: ANN202
        headers = ORIGIN | {"X-Recovery-Key": key, "Content-Type": "application/octet-stream"}
        return c.post("/api/start/restore", content=body, headers=headers)

    # A wrong key, a damaged file or no file: nothing changes, nothing is left behind.
    for body, key in ((data, str(x25519.Identity.generate())), (data[:-200], recovery),
                      (b"", recovery)):  # fmt: skip
        r = restore(body, key)
        assert r.status_code == 400 and state.store is None, r.text
        assert sorted(p.name for p in home.parent.iterdir()) == ["vault"]
        assert [p.name for p in home.iterdir()] == ["inbox"]

    r = restore(data, recovery)
    assert r.status_code == 200 and state.store is not None, r.text
    store = state.store
    assert store.get("taxpayer", "grantor-trust")[0].kind == "trust"
    doc = store.list("document")[0]
    assert store.blobs.get(doc.sha256).startswith(b"%PDF")
    assert any(ch.message == "test household" for ch in store.vault.history.changes())
    assert [r.slug for r in list_entities(home)][1:] == ["northwind-holdings-inc"]
    assert not list(home.parent.glob(".restoring-*"))
    # Never over a vault that's there.
    assert restore(data, recovery).status_code == 400
    store.close()


def test_start_is_this_macs_only() -> None:
    from taxbinder.web.app import LOCAL_ONLY

    for path in ("/api/start", "/api/start/unlock", "/api/start/restore", "/start/restore",
                 "/start/unlock"):  # fmt: skip
        assert path in LOCAL_ONLY
