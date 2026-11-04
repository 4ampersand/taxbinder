"""Check through the season: files arriving in the inbox folder or from a portal
window wait for Check like Add documents, the side menu says how many, and a later round files
only the new ones as one change; a file attached to something chosen is filed at once
(fictional data, PDFs made in the test)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from test_check import drop, k1
from test_web import ORIGIN, PORT, client

from taxbinder import check, journey
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState
from taxbinder.web.watcher import process_inbox


def _arrive(folder: Path, name: str, data: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    old = time.time() - 60  # settled: not still being written
    os.utime(path, (old, old))


def _check_step(state: AppState) -> journey.Step:
    steps = journey.steps(state.store, 2025, state.vault_path, state.today())
    return next(s for s in steps if s.id == "check")


def test_arrivals_wait_for_check_and_a_later_round_files_them(tmp_path: Path) -> None:
    store = RecordStore(new_vault(tmp_path / "vault"))
    build(store, "test")
    drop(store, "first.pdf", k1(1, "Corbel Net Lease"))
    check.confirm(store, "test")  # the first round, filed
    state = AppState(vault_path=store.vault.root, store=store, port=PORT, emit=lambda e: None)
    assert _check_step(state).badge == ""

    inbox = store.vault.inbox
    _arrive(
        inbox,
        "dropped.pdf",
        make_pdf(  # the household's Fernhollow K-1: it has a row
            ["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"]
        ),
    )
    _arrive(inbox / "portal", "statement.pdf", make_pdf(["Form 1099-INT 2025", "Tidemark"]))
    got = process_inbox(state)
    assert len(got) == 2
    assert [g.linked_to for g in got if g.linked_to]  # matched to its row meanwhile
    waiting = check.waiting(store)
    assert {d.acquired.via for d in waiting} == {"drop_folder", "portal_window"}
    step = _check_step(state)
    assert step.badge == "2 new" and not step.done

    c = client(state)
    page = c.get("/check").text
    assert "2 new documents since your last Check" in page and 'id="household"' not in page
    before = {d.id for d in store.list("document") if not d.waiting}
    r = c.post("/api/check/confirm", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["filed"] == 2, r.text
    assert not check.waiting(store) and _check_step(state).badge == ""
    assert (
        c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN).status_code
        == 200
    )
    assert len(check.waiting(store)) == 2  # the round undone on its own; the first stands
    assert before == {d.id for d in store.list("document") if not d.waiting}
    store.close()


def test_a_question_shows_before_new(tmp_path: Path) -> None:
    store = RecordStore(new_vault(tmp_path / "vault"))
    build(store, "test")
    state = AppState(vault_path=store.vault.root, store=store, port=PORT, emit=lambda e: None)
    _arrive(store.vault.inbox, "letter.pdf", make_pdf(["Dear friend", "Thank you for your visit"]))
    process_inbox(state)
    assert _check_step(state).badge == "1 question"
    store.close()


def test_a_file_attached_to_a_row_is_filed_at_once(tmp_path: Path) -> None:
    store = RecordStore(new_vault(tmp_path / "vault"))
    build(store, "test")
    state = AppState(vault_path=store.vault.root, store=store, port=PORT, emit=lambda e: None)
    row = store.list("expectation", 2025)[0]
    r = client(state).post(
        f"/api/upload?target={row.id}",
        content=k1(9, "Corbel Net Lease"),
        headers=ORIGIN | {"Content-Type": "application/octet-stream", "X-Filename": "k1.pdf"},
    )
    assert r.status_code == 200, r.text
    assert not check.waiting(store)  # you said where it goes
    store.close()
