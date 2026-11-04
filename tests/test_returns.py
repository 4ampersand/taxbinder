"""Tax returns recognised by their titles: never as the forms they mention."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from kformgen import k1
from pdfgen import make_pdf
from quills import build
from samples.generate import plan, returns

from taxbinder import doctypes
from taxbinder.ingest.classify import classify
from taxbinder.ingest.inbox import ingest_file
from taxbinder.ingest.pdftext import pdf_info
from taxbinder.package.build import build_package
from taxbinder.returns import returns_for_year, set_details
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)


def read(*pages: list[str]):  # noqa: ANN201
    return classify(pdf_info(make_pdf(*pages))[1], "scan.pdf", [], 2026)


def test_the_sample_returns() -> None:
    for data, answer in returns(plan(random.Random(7))):
        c = classify(pdf_info(data)[1], "return.pdf", [], 2026)
        assert (c.doc_type, c.tax_year) == ("tax_return", answer["year"])
        assert c.tax_return is not None and c.tax_return.form == answer["form"]
        state = answer.get("state") if answer["form"] == "IT-201" else None
        assert c.tax_return.jurisdiction == (state or "federal")
    ny = returns(plan(random.Random(7)))[1][0]
    assert classify(pdf_info(ny)[1], "ny.pdf", [], 2026).tax_return.resident is True


def test_titles_not_form_numbers() -> None:
    assert classify(pdf_info(k1())[1], "k1.pdf", [], 2026).doc_type == "k1_1065"
    letter = read(
        [
            "Fernhollow Properties LP",
            "Enclosed is your 2025 Schedule K-1 (Form 1065). Keep it with your records for",
            "your 2025 U.S. Individual Income Tax Return.",
        ]
    )
    assert letter.doc_type == "k1_1065"  # a cover letter: the K-1 is named first
    partnership = read(
        ["Form 1065   U.S. Return of Partnership Income   2025", "Name of partnership"],
        ["Schedule K-1 (Form 1065) 2025   Partner's Share of Income"],
    )
    assert partnership.doc_type == "tax_return" and partnership.tax_return.form == "1065"
    w2 = read(
        [
            "Copy 2 To Be Filed With Employee's California State, City, or Local Income Tax Return",
            "Form W-2 Wage and Tax Statement 2025",
        ]
    )
    assert w2.doc_type == "w2"


def test_amended_draft_and_state_returns() -> None:
    x = read(["Form 1040-X   Amended U.S. Individual Income Tax Return", "Calendar year 2024"])
    assert x.tax_return.form == "1040-X" and x.tax_return.amended and x.tax_year == 2024
    draft = read(
        [
            "DO NOT FILE",
            "Form 1040   U.S. Individual Income Tax Return   2025",
            "For the year Jan. 1-Dec. 31, 2025",
        ]  # fmt: skip
    )
    assert draft.tax_return.status == "draft" and draft.tax_year == 2025
    ca = read(
        ["Form 540NR   California Nonresident or Part-Year Resident Income Tax Return   2025"]
    )
    info = ca.tax_return
    assert (info.jurisdiction, info.form, info.resident, ca.tax_year) == (
        "CA",
        "540NR",
        False,
        2025,
    )
    wv = read(["West Virginia Resident Income Tax Return   2025   Form IT-140"])
    assert wv.tax_return.jurisdiction == "WV"


def test_return_papers() -> None:
    e_file = read(["Form 8879   IRS e-file Signature Authorization   2025"])
    assert e_file.doc_type == "return_paper" and e_file.tax_return.form == "8879"
    extension = read(
        [
            "Form 4868   Application for Automatic Extension of Time To File U.S. Individual",
            "Income Tax Return   2025",
        ]  # fmt: skip
    )
    assert extension.doc_type == "return_paper" and extension.tax_return.form == "4868"
    voucher = read(
        [
            "Form 1040-ES   Department of the Treasury   Internal Revenue Service",
            "2026 Estimated Tax for Individuals",
        ]  # fmt: skip
    )
    assert (voucher.doc_type, voucher.tax_return.form, voucher.tax_year) == (
        "return_paper",
        "1040-ES",
        2026,
    )
    ca = read(["Form 540-ES   California   2026 Estimated Tax for Individuals"])
    assert (ca.tax_return.jurisdiction, ca.tax_return.form) == ("CA", "540-ES")


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s, "test")
    yield s
    s.close()


def drop(store: RecordStore, name: str, *pages: list[str]) -> str:
    path = store.vault.inbox / name
    path.write_bytes(make_pdf(*pages))
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


FED = ["Form 1040   U.S. Individual Income Tax Return   2025", "For the year Jan. 1-Dec. 31, 2025",
       "1a Total amount from Form(s) W-2, box 1 . . . 61,250."]  # fmt: skip
NY = ["Form IT-201   Resident Income Tax Return   New York State   2025"]
E_FILE = ["Form 8879   IRS e-file Signature Authorization   2025"]


def test_returns_section_and_status(store: RecordStore) -> None:
    fed, ny, paper = (
        drop(store, "1040.pdf", FED),
        drop(store, "ny.pdf", NY),
        drop(store, "e.pdf", E_FILE),
    )
    doc, _ = store.get("document", fed)
    assert doc.classification.doc_type == "tax_return" and doc.classification.source is None
    linked = {x.doc for e in store.list("expectation") for x in e.evidence}
    assert fed not in linked  # a return is no checklist row's document, though it names a W-2
    [federal, state] = returns_for_year(store, 2025)
    assert (federal.doc.id, state.doc.id) == (fed, ny)
    assert [p.id for p, _ in federal.papers] == [paper]
    set_details(store, fed, "test", status="filed")
    sha = store.completed[-1].sha
    assert store.get("document", fed)[0].tax_return.status == "filed"
    store.undo(sha, "test")
    assert store.get("document", fed)[0].tax_return.status == "unsure"
    with pytest.raises(ValueError, match="draft, filed"):
        set_details(store, fed, "test", status="maybe")


def test_package_has_the_years_returns(store: RecordStore, tmp_path: Path) -> None:
    drop(store, "1040.pdf", FED)
    drop(store, "old.pdf", ["Form 1040   U.S. Individual Income Tax Return   2024"])
    result = build_package(store, 2025, tmp_path / "pkg", "test", now=NOW)
    assert result.files.get("00-Returns") == 1  # this year's only, not last year's
    assert [p.name for p in (tmp_path / "pkg" / "00-Returns").iterdir()]


def test_own_return_type_moves_to_the_built_in_one(store: RecordStore) -> None:
    own = doctypes.add_doc_type(store, "Tax returns", "09-Other", ["income tax return"], "test")
    path = store.vault.inbox / "fed.pdf"
    path.write_bytes(make_pdf(FED))
    doc_id = ingest_file(store, path, "upload", "test", now=NOW).doc_id
    doc, rev = store.get("document", doc_id)
    with store.operation("test", "an added type for returns"):  # filed under a type of their own
        c = doc.classification.model_copy(update={"doc_type": own.id})
        store.put(doc.model_copy(update={"classification": c, "tax_return": None}), rev)
    assert doctypes.looks_like_return(own)
    assert doctypes.merge_doc_type(store, own.id, "tax_return", "test") == 1
    sha = store.completed[-1].sha
    moved, _ = store.get("document", doc_id)
    assert moved.classification.doc_type == "tax_return" and moved.tax_return.form == "1040"
    assert store.list("doctype") == []  # nothing uses it any more
    store.undo(sha, "test")
    assert store.get("document", doc_id)[0].classification.doc_type == own.id
    assert [d.id for d in store.list("doctype")] == [own.id]


def test_documents_page(store: RecordStore) -> None:
    from test_web import client

    drop(store, "1040.pdf", FED)
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    c = client(state)
    page = c.get("/documents?year=2025").text
    assert "Returns · 2025" in page and "Form 1040" in page
    assert "not matched" not in page  # the return isn't counted as unmatched
    assert "Returns · 2025" in c.get("/documents?year=2025").text
    doc = next(d for d in store.list("document") if d.tax_return)
    r = c.post(
        "/api/returns/edit",
        json={"id": doc.id, "status": "filed"},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert r.status_code == 200 and r.json()["undo"]


def test_a_return_typed_by_hand_shows_under_returns(store: RecordStore) -> None:
    from taxbinder.review import edit_document

    doc_id = drop(store, "notes.pdf", ["Our 2025 return, as sent to us"])  # no title to read
    edit_document(store, doc_id, "test", "tax_return", 2025)
    [r] = returns_for_year(store, 2025)
    assert r.doc.id == doc_id and (r.info.form, r.info.status) == ("1040", "unsure")
