"""The household in the app: worked out, confirmed, and rows before and after."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from helpers import new_vault
from samples.generate import plan, returns
from test_parties import form

from taxbinder.household import Person, confirm, worked_out
from taxbinder.ingest.inbox import ingest_file
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.web.app import AppState
from taxbinder.years import add_row

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
PACKET = returns(plan(random.Random(7)))[0][0]


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))  # a fresh vault: nobody in it
    yield s
    s.close()


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    when = NOW + timedelta(minutes=len(store.list("document")))  # in the order they came
    return ingest_file(store, path, "upload", "test", now=when).doc_id


def test_a_fresh_vault_from_rows_to_household(store: RecordStore) -> None:
    before = add_row(store, 2025, "Tamarack Savings Bank", "Savings", "1099_int", "", "test")
    assert store.get("source", store.get("expectation", before)[0].source)[0].owner == "household"
    rowan = drop(store, "r.pdf", form("Kestrel Media Group", recipient="Rowan Quill"))
    assert store.get("document", rowan)[0].recipient.ask  # nobody known yet: "Whose? …"
    drop(store, "1040.pdf", PACKET)
    h = worked_out(store, 2025)
    assert (h.found_in, h.return_year, h.filing_status, h.state) == (
        "return",
        2024,
        "married_filing_jointly",
        "NY",
    )
    assert [(p.name, p.role, p.last4) for p in h.people] == [
        ("Avery Quill", "taxpayer", "0101"),
        ("Jordan Quill", "spouse", "0102"),
        ("Rowan Quill", "dependent", "0103"),
    ]
    confirm(store, h, 2025, "test")
    sha = store.completed[-1].sha
    people = {t.id: t for t in store.list("taxpayer")}
    assert set(people) == {"primary", "spouse", "rowan-quill"}
    assert people["primary"].last4 == "0101" and people["primary"].tin is None  # last 4 only
    for y in (2024, 2025):
        config = store.get("year", str(y))[0]
        assert (config.filing_status, config.resident_states) == ("married_filing_jointly", ["NY"])
    assert store.get("document", rowan)[0].recipient.taxpayers == ["rowan-quill"]
    assert worked_out(store, 2025).confirmed
    after = add_row(store, 2025, "Linden Education", "Jordan", "w2", "spouse", "test")
    assert store.get("source", store.get("expectation", after)[0].source)[0].owner == "spouse"
    store.undo(store.completed[-1].sha, "test")
    store.undo(sha, "test")  # the household, as one change
    assert store.list("taxpayer") == [] and store.get("document", rowan)[0].recipient.ask
    with pytest.raises(RecordNotFound):
        store.get("year", "2024")


def test_worked_out_from_the_documents(store: RecordStore) -> None:
    assert worked_out(store, 2025).found_in == "nothing"
    drop(store, "a.pdf", form("Gannet Home Lending", recipient="AVERY QUILL & JORDAN QUILL"))
    drop(store, "t.pdf", form("Tamarack Savings Bank", recipient="Quill Family Trust"))
    h = worked_out(store, 2025)
    assert (h.found_in, h.documents) == ("documents", 2)
    assert [(p.name, p.kind) for p in h.people] == [
        ("Avery Quill", "individual"), ("Jordan Quill", "individual"),
        ("Quill Family Trust", "trust"),
    ]  # fmt: skip
    assert not h.complete  # who's who is asked, never guessed
    with pytest.raises(ValueError, match="who the taxpayer is"):
        confirm(store, h, 2025, "test")
    h.people[0].role, h.people[1].role = "taxpayer", "spouse"
    confirm(store, h, 2025, "test")
    kinds = {t.id: t.kind for t in store.list("taxpayer")}
    assert kinds == {"primary": "individual", "spouse": "individual", "quill-family-trust": "trust"}


def test_an_answered_document_stays_and_a_busy_person_stays(store: RecordStore) -> None:
    from taxbinder.whose import set_whose

    confirm_as = [Person("Avery Quill", "taxpayer"), Person("Jordan Quill", "spouse")]
    h = worked_out(store, 2025)
    h.people = confirm_as
    confirm(store, h, 2025, "test")
    doc = drop(store, "r.pdf", form("Kestrel Media Group", recipient="Rowan Quill"))
    set_whose(store, doc, ["spouse"], "test")  # a person said so
    h = worked_out(store, 2025)
    h.people.append(Person("Rowan Quill", "dependent"))
    confirm(store, h, 2025, "test")
    assert store.get("document", doc)[0].recipient.taxpayers == ["spouse"]  # never changed
    add_row(store, 2025, "Linden Education", "Jordan", "w2", "spouse", "test")
    h = worked_out(store, 2025)
    h.people = [p for p in h.people if p.id != "spouse"]
    with pytest.raises(ValueError, match="Jordan Quill has rows or documents"):
        confirm(store, h, 2025, "test")


def test_the_overview_box(store: RecordStore) -> None:
    from test_web import ORIGIN, client

    drop(store, "1040.pdf", PACKET)
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    c = client(state)
    page = c.get("/check?year=2025").text
    assert "Is this right?" in page and "from your 2024 return" in page
    assert "Rowan Quill" in page and "SSN …0103" in page and "Looks right" in page
    body = {
        "year": 2025, "return_year": 2024, "filing_status": "married_filing_jointly",
        "state": "NY", "people": [{"name": "Avery Quill", "role": "taxpayer"}],
    }  # fmt: skip
    assert c.post("/api/household/confirm", json=body, headers=ORIGIN).status_code == 200
    assert "Is this right?" not in c.get("/check?year=2025").text  # confirmed: not asked again
    page = c.get("/settings?year=2025").text
    assert "Is this right?" not in page and "<b>Household:</b> Avery Quill" in page
    assert "New York" in page
    bad = {**body, "people": [{"name": "Avery Quill", "role": "boss"}]}
    assert c.post("/api/household/confirm", json=bad, headers=ORIGIN).status_code == 400
