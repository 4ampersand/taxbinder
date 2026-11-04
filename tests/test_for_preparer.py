"""For my preparer: papers handed over as they are, with a note (fictional data).
The preparer's page and zip are tested in test_preparer.py."""

from __future__ import annotations

import csv
from pathlib import Path

from pdfgen import make_pdf
from test_web import (  # noqa: F401 - `state` is a fixture
    KETTLE,
    ORIGIN,
    PIKEWAY,
    _upload,
    client,
    state,
)

from taxbinder import preparer
from taxbinder.ingest.inbox import rematch
from taxbinder.package.build import build_package
from taxbinder.web.app import AppState


def _paper(c, data: bytes, name: str, year: int, note: str = ""):  # noqa: ANN001, ANN202
    headers = {**ORIGIN, "X-Filename": name, "Content-Type": "application/octet-stream"}
    return c.post("/api/preparer", params={"year": year, "note": note}, content=data,
                  headers=headers)  # fmt: skip


def test_a_paper_is_kept_as_it_is(state: AppState, tmp_path: Path) -> None:  # noqa: F811
    c, s = client(state), state.store
    # it reads like a K-1, but a paper for the preparer is never typed or matched
    k1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    r = _paper(c, k1, "statement.pdf", 2025, "FBAR: savings 12345678, highest balance")
    assert r.status_code == 200, r.text
    doc_id, undo = r.json()["id"], r.json()["undo"]
    d, _ = s.get("document", doc_id)
    assert preparer.is_paper(d) and not d.waiting and d.classification.doc_type is None
    assert d.classification.tax_year == 2025 and d.preparer_note.startswith("FBAR")
    assert all(x.doc != doc_id for x in s.get("expectation", KETTLE)[0].evidence)
    assert rematch(s, "t") == [] or all(x.doc_id != doc_id for x in rematch(s, "t"))
    assert doc_id not in c.get("/check").text  # never asked about
    home = c.get("/?year=2025").text
    assert "For my preparer" in home and "FBAR: savings 12345678, highest balance" in home
    docs = c.get("/documents?year=2025").text
    assert "For my preparer" in docs and "act-pp-unmark" in docs
    # handed over under its note, account-like digits dropped; the note in the index
    root = build_package(s, 2025, tmp_path / "pkg", "t").root
    files = [p.name for p in (root / "10-For-preparer").iterdir()]
    assert files == ["FBAR savings, highest balance.pdf"]
    with (root / "index.csv").open() as f:
        row = next(r for r in csv.DictReader(f) if r["document"] == doc_id)
    assert row["file"] == "10-For-preparer/FBAR savings, highest balance.pdf"
    assert row["note"].startswith("FBAR")
    # one change: one Undo takes it away
    assert c.post("/api/undo", json={"sha": undo["sha"]}, headers=ORIGIN).status_code == 200
    assert doc_id not in {x.id for x in s.list("document")}


def test_a_file_in_the_binder_can_be_marked_and_unmarked(state: AppState) -> None:  # noqa: F811
    c, s = client(state), state.store
    doc_id = _upload(c, make_pdf(["a letter from the county"]), "letter.pdf").json()["doc_id"]
    r = c.post("/api/preparer/edit", json={"id": doc_id, "what": "mark", "year": 2025,
                                           "note": "Closing letter"}, headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text
    d, _ = s.get("document", doc_id)
    assert preparer.is_paper(d) and d.preparer_note == "Closing letter"
    r = c.post("/api/preparer/edit", json={"id": doc_id, "what": "note", "note": "  A  letter "},
               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200 and s.get("document", doc_id)[0].preparer_note == "A letter"
    r = c.post("/api/preparer/edit", json={"id": doc_id, "what": "unmark"}, headers=ORIGIN)
    d, _ = s.get("document", doc_id)
    assert r.status_code == 200 and not preparer.is_paper(d) and d.waiting  # back to Check
    assert d.preparer_note is None
    # a row's own document stays that row's
    on_row = _upload(c, make_pdf(["a K-1"]), "k1.pdf", expectation=PIKEWAY, year="2025")
    on_row = on_row.json()["doc_id"]
    r = c.post("/api/preparer/edit", json={"id": on_row, "what": "mark", "year": 2025},
               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 400 and "attached" in r.json()["error"]
    long = {"id": doc_id, "what": "mark", "year": 2025, "note": "x" * 121}
    assert c.post("/api/preparer/edit", json=long, headers=ORIGIN).status_code == 400
    assert _paper(c, b"just text", "notes.pdf", 2025).status_code == 415


def test_last_years_papers_are_a_reminder(state: AppState) -> None:  # noqa: F811
    from taxbinder.years import new_year

    c, s = client(state), state.store
    _paper(c, make_pdf(["statement one"]), "a.pdf", 2025, "FBAR: savings")
    _paper(c, make_pdf(["statement two"]), "b.pdf", 2025)
    new_year(s, 2026, 2025, "test")
    notice, text = preparer.reminder(s, 2026)
    assert notice == "preparer:2026" and "2 papers" in text and "FBAR: savings, b.pdf" in text
    assert preparer.reminder(s, 2025) is None  # none the year before
    assert "Any for 2026?" in c.get("/?year=2026").text
    r = c.post("/api/notice", json={"id": notice, "action": "dismiss"}, headers=ORIGIN)
    assert r.status_code == 200 and preparer.reminder(s, 2026) is None
    assert "Any for 2026?" not in c.get("/?year=2026").text
