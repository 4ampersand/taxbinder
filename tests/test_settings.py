"""The Settings page: defaults that keep vaults as they were, and each setting."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers import new_vault
from quills import build

from taxbinder import settings
from taxbinder.entities import create_entity
from taxbinder.matrix import rows as year_rows
from taxbinder.matrix import set_boxes
from taxbinder.review import set_verified
from taxbinder.store import RecordStore, keys_from_env
from taxbinder.web.app import AppState
from taxbinder.years import add_row


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    yield s
    s.close()


def app(store: RecordStore, **kw):  # noqa: ANN201
    from test_web import client

    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2), **kw,
    )  # fmt: skip
    return state, client(state)


def test_defaults_keep_a_vault_as_it_was(store: RecordStore) -> None:
    base = store.vault.root
    assert not settings.other_filers_on(store, base) and not settings.ticking_on(store)
    create_entity(base, keys_from_env(), "Quill Family Trust", "1041", "test")
    assert settings.other_filers_on(store, base)  # a vault that has one keeps it shown
    build(store, "test")
    row = next(e for e in store.list("expectation", 2025) if e.acquisition == "vaulted")
    set_verified(store, [row.id], True, "", "test")
    assert settings.ticking_on(store)  # someone ticks already
    settings.change(store, "test", "tick_documents", "off")
    assert not settings.ticking_on(store)
    assert store.get("expectation", row.id)[0].review == "verified"  # the tick stays
    store.undo(store.completed[-1].sha, "test")
    assert settings.ticking_on(store)
    with pytest.raises(ValueError, match="unknown setting"):
        settings.change(store, "test", "colour", "blue")


def test_other_filers_off_hides_the_switcher_and_move(store: RecordStore) -> None:
    from test_parties import form

    from taxbinder.ingest.inbox import ingest_file

    build(store, "test")
    create_entity(store.vault.root, keys_from_env(), "Quill Family Trust", "1041", "test")
    path = store.vault.inbox / "t.pdf"
    path.write_bytes(form("Tamarack Savings Bank", recipient="Quill Family Trust"))
    ingest_file(store, path, "upload", "test")
    state, c = app(store)
    page = c.get("/documents?year=2025").text
    assert "Filing for" in page and "Add an entity" in page and "act-move" in page
    from test_web import ORIGIN

    r = c.post("/api/settings", json={"key": "other_filers", "value": "off"}, headers=ORIGIN)
    assert r.status_code == 200 and "undo" in r.json()
    page = c.get("/documents?year=2025").text
    assert "Filing for" not in page and "Add an entity" not in page and "act-move" not in page
    state.close_all()


def test_ticking_off_hides_verify(store: RecordStore) -> None:
    build(store, "test")
    state, c = app(store)
    settings.change(store, "test", "tick_documents", "on")
    from test_web import rows_of

    assert '"verify" data-verified="true"' in rows_of(c, state)
    settings.change(store, "test", "tick_documents", "off")
    assert '"verify" data-verified="true"' not in rows_of(c, state)
    state.close_all()


def test_websites_in_the_browser(store: RecordStore) -> None:
    from test_web import ORIGIN

    opened, events = [], []
    state, c = app(store, open_url=opened.append)
    state.emit = events.append
    url = {"url": "https://portal.example.com/statements"}
    c.post("/api/open-portal", json=url, headers=ORIGIN)
    assert events and not opened  # an app window, as before
    settings.change(store, "test", "websites_open_in", "browser")
    r = c.post("/api/open-portal", json=url, headers=ORIGIN)
    assert r.json()["browser"] and opened == ["https://portal.example.com/statements"]
    assert len(events) == 1
    state.close_all()


def test_the_usual_box_moves_rows_still_on_it(store: RecordStore) -> None:
    build(store, "test")
    add_row(store, 2025, "Brightwater Analytics", "Avery", "w2", "primary", "test")
    add_row(store, 2025, "Kestrel Labs", "Jordan", "w2", "spouse", "test")
    w2s = [r.source.id for r in year_rows(store) if r.doc_type == "w2"]
    key = ["1", "2", "16", "17"]  # wages first, then tax withheld (the W-2's key boxes)
    assert len(w2s) >= 3 and all(r.boxes == key for r in year_rows(store) if r.doc_type == "w2")
    set_boxes(store, w2s[0], "w2", "5", "test")  # the person chose box 5 for this one
    set_boxes(store, w2s[1], "w2", "1", "test")  # and box 1 for this one: their choice too
    settings.change(store, "test", "usual_box.w2", "2")
    boxes = {r.source.id: r.boxes for r in year_rows(store) if r.doc_type == "w2"}
    assert boxes[w2s[0]] == ["5"] and boxes[w2s[1]] == ["1"]
    assert boxes[w2s[2]] == ["2", "1", "16", "17"]  # the new usual box first, the key ones kept
    assert settings.usual_box(store, "w2") == "2"
    with pytest.raises(ValueError, match="isn't a box"):
        settings.change(store, "test", "usual_box.w2", "99")


def test_a_move_between_states_and_the_page(store: RecordStore) -> None:
    from test_web import ORIGIN

    build(store, "test")
    state, c = app(store)
    move = {"year": 2025, "filing_status": "married_filing_jointly", "states": ["NY", "NJ"],
            "moved_on": "2025-07-01"}  # fmt: skip
    assert c.post("/api/years/set", json=move, headers=ORIGIN).status_code == 200
    config = store.get("year", "2025")[0]
    assert config.resident_states == ["NY", "NJ"] and config.moved_on == date(2025, 7, 1)
    bad = {**move, "moved_on": None}
    assert c.post("/api/years/set", json=bad, headers=ORIGIN).status_code == 400
    page = c.get("/settings?year=2025").text
    assert "Advanced" in page and "Usual box per form" in page and "GNU Affero" in page
    assert "Is this right?" in page or "<b>Household:</b>" in page
    assert 'value="2025-07-01"' in page
    state.close_all()
