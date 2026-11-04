"""What a 1040 says, and the documents it lists against the vault."""

from __future__ import annotations

import logging
import random
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from samples.generate import _render, plan, returns, scan
from samples.household import ENTITIES, FISCAL, MISSING_2024

from taxbinder.extract.return1040 import read_1040
from taxbinder.ingest.inbox import ingest_file
from taxbinder.ingest.pdftext import pdf_info
from taxbinder.models import Taxpayer
from taxbinder.returns import add_listed, listed_for
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
DOCS = plan(random.Random(7))
(PACKET, ANSWER), (NY, _), (BARE, _) = returns(DOCS)


def test_the_sample_1040() -> None:
    read = read_1040(pdf_info(PACKET, 80)[1])
    assert read.filing_status == "married_filing_jointly" and read.state == "NY"
    assert {p.role: p.name for p in read.people} == ANSWER["names"]
    assert [p.last4 for p in read.people] == ["0101", "0102", "0103"]  # never the whole SSN
    names = [x.name for x in read.listed]
    assert len(names) == 18 and "Mossgrove Lending Fund LP" in names  # on the statement
    by_name = {x.name: x for x in read.listed}
    assert by_name["Larkmoor Securities LLC"].doc_type == "1099_composite"  # interest and dividends
    assert by_name["Tamarack Savings Bank"].doc_type == "1099_int"
    assert by_name["Nettlefield Design Inc."].doc_type == "k1_1120s"  # an S corporation
    assert by_name["Hollis Quill Legacy Trust"].doc_type == "k1_1041"
    assert by_name["Alderpoint Timber Fund LP"].ein == "00-0000201"
    assert read.totals_only == ["wages", "retirement", "social_security", "mortgage_interest"]


def test_a_bare_1040_names_no_issuer() -> None:
    read = read_1040(pdf_info(BARE, 80)[1])
    assert read.listed == [] and read.totals_only == ["wages", "interest", "dividends"]
    assert read_1040("Form 1040 2024\nWages 100.").people == []  # nothing there: nothing read
    caption = "Your first name and middle initial Last name\nYour social security number\n"
    assert read_1040(caption).people == []  # a caption is never a name


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    with s.operation("test", "household"):
        s.put(Taxpayer(id="primary", name="Avery Quill"), None)
    yield s
    s.close()


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


def test_the_two_missing_k1s(store: RecordStore, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ids = {e[0] for e in ENTITIES}
    k1s = [d for d in DOCS if d.year == 2024 and d.issuer in ids and not d.corrected]
    assert len(k1s) == 12 and FISCAL in {d.issuer for d in k1s}  # filed by its year's end
    for i, d in enumerate(k1s):
        drop(store, f"k1-{i}.pdf", _render(d, random.Random(i)))
    doc = drop(store, "1040.pdf", PACKET)
    info = store.get("document", doc)[0].tax_return
    missing = [s.item.name for s in listed_for(store, info, 2024) if not s.here]
    names = dict((e[0], e[1]) for e in ENTITIES)
    # the K-1s, and the 1099s on Schedule B that aren't in this vault
    assert {names[m] for m in MISSING_2024} <= set(missing)
    assert not {names[d.issuer] for d in k1s} & set(missing)
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("taxbinder"))
    assert "Kittiwake" not in ours and "0000201" not in ours

    added = add_listed(store, doc, 2024, "test")  # the return's own year: only the missing
    assert added == len(missing)
    sha = store.completed[-1].sha
    assert add_listed(store, doc, 2024, "test") == 0  # twice: nothing twice
    assert add_listed(store, doc, 2025, "test") == 18  # next year: everything it lists
    store.undo(store.completed[-1].sha, "test")
    store.undo(sha, "test")
    assert store.list("expectation", 2024) == [] and store.list("expectation", 2025) == []
    institutions = {i.id for i in store.list("institution")}
    assert "kittiwake-infrastructure" not in institutions  # its new institution went too


def test_a_scanned_return_isnt_read_inside(store: RecordStore) -> None:
    doc = store.get("document", drop(store, "scan.pdf", scan(BARE)))[0]
    assert doc.tax_return is None or doc.tax_return.read is None
    state = drop(store, "ny.pdf", NY)
    assert store.get("document", state)[0].tax_return.read is None  # state returns: recognised, not read


def test_returns_section(store: RecordStore, tmp_path: Path) -> None:
    from test_web import ORIGIN, client

    doc = drop(store, "1040.pdf", PACKET)
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    c = client(state)
    page = c.get("/documents?year=2024").text
    assert "Married filing jointly · Avery Quill · Jordan Quill · Rowan Quill (dependent)" in page
    assert "Listed on the return: 18" in page and "no document yet 18" in page
    assert "You had W-2 wages: add your employers." in page
    assert "Add the 18 missing to 2024’s checklist" in page and "Add 18 to 2025" in page
    r = c.post("/api/returns/add-listed", json={"id": doc, "year": 2025}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["added"] == 18
