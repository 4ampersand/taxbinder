"""Across the years: each row's referenced box read from every year's document, the
marks for years without one, big changes, scanned amounts marked; a read value never replaces a
typed one (the invented household; PDFs made here)."""

from __future__ import annotations

import random
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import new_vault
from samples.generate import _render, plan
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder import matrix, settings
from taxbinder.extract.figures import read_boxes
from taxbinder.ingest.classify import classify
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Expectation, Institution, Source, Taxpayer
from taxbinder.models.matrix import FigureSet, Suggestion
from taxbinder.review import link_document
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

DOCS = plan(random.Random(7))
TODAY = date(2026, 3, 1)
W2 = "brightwater-analytics.avery"


def _sample(doc_type: str, year: int, issuer: str) -> bytes:
    d = next(d for d in DOCS if (d.doc_type, d.year, d.issuer, d.layout) ==
             (doc_type, year, issuer, "tuned"))  # fmt: skip
    return _render(d, random.Random(1))


def _add(store: RecordStore, source: str, doc_type: str, year: int, data: bytes | None) -> None:
    """A checklist row for the year, and its document when there's one."""
    exp = f"{year}/{source}.{doc_type.replace('_', '-')}"
    with store.operation("test", f"row {exp}"):
        store.put(Expectation(id=exp, year=year, source=source, doc_type=doc_type), None)
    if data is not None:
        path = store.vault.inbox / f"{source} {year}.pdf"
        path.write_bytes(data)
        doc = ingest_file(store, path, "upload", "test").doc_id
        if not any(x.doc == doc for x in store.get("expectation", exp)[0].evidence):
            link_document(store, doc, exp, "test")


def _row(store: RecordStore, key: str) -> matrix.Row:
    return next(r for r in matrix.rows(store, TODAY) if r.key == key)


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    with s.operation("test", "household"):
        s.put(Taxpayer(id="primary", name="Avery Quill"), None)
        for inst, name in (("brightwater-analytics", "Brightwater Analytics Inc."),
                           ("quarry-hill-funds", "Quarry Hill Funds")):  # fmt: skip
            s.put(Institution(id=inst, name=name), None)
        s.put(Source(id=W2, institution="brightwater-analytics", name="Avery", owner="primary"),
              None)  # fmt: skip
        s.put(Source(id="quarry-hill-funds.joint", institution="quarry-hill-funds", name="Joint",
                     owner="primary"), None)  # fmt: skip
    for y in (2023, 2024, 2025):
        _add(s, W2, "w2", y, _sample("w2", y, "brightwater-analytics"))
    yield s
    s.close()


def test_a_changed_box_shows_for_every_year_and_typed_values_stay(store: RecordStore) -> None:
    key = f"{W2}|w2"
    matrix.read_pdfs(store, "test")  # as when the documents arrive
    row = _row(store, key)
    key_boxes = ["1", "2", "16", "17"]  # the usual box first, then the W-2's key ones
    assert row.boxes == key_boxes and row.usual == "1" and not row.own
    wages = {d.year: d.figures["1"] for d in DOCS if d.doc_type == "w2" and
             d.issuer == "brightwater-analytics" and d.layout == "tuned"}  # fmt: skip
    assert {y: c.amount for y, c in row.cells.items()} == wages
    assert all(c.read and c.first is None for c in row.cells.values())  # read, nothing saved
    assert ("12a", "See instructions for box 12") in row.options

    matrix.set_cell(store, W2, "w2", 2024, "7777", "test")  # typed: it stays
    began = time.perf_counter()
    matrix.set_boxes(store, W2, "w2", "12a", "test")  # the drop-down
    assert time.perf_counter() - began < 1  # read at once from all three years
    row = _row(store, key)
    assert row.own and row.boxes == ["12a"]
    assert row.cells[2023].amount == 6000 and row.cells[2025].amount == 7000
    cell = row.cells[2024]
    assert cell.amount == 7777 and cell.conflict and cell.suggested == [Decimal(6500)]
    store.undo(store.completed[-1].sha, "test")  # one change: the box and its reading
    assert _row(store, key).boxes == key_boxes

    # Settings → Advanced: a row not set by the person follows the usual box, read at once
    settings.change(store, "test", "usual_box.w2", "2")
    row = _row(store, key)
    assert row.boxes == ["2", "1", "16", "17"] and row.cells[2023].amount == next(
        d.figures["2"] for d in DOCS if (d.doc_type, d.year, d.issuer) ==
        ("w2", 2023, "brightwater-analytics"))  # fmt: skip


def test_the_marks_for_years_without_an_amount(store: RecordStore) -> None:
    source = "quarry-hill-funds.joint"
    _add(store, source, "1099_composite", 2023, None)  # never came
    _add(store, source, "1099_composite", 2025, None)  # still to come
    with store.operation("test", "not that year"):
        store.put(Expectation(id=f"2024/{W2}.1099-int", year=2024, source=W2, doc_type="1099_int",
                              applicability="not_applicable", applicability_note="closed"), None)
    marks = {y: c.mark for y, c in _row(store, f"{source}|1099_composite").cells.items()}
    assert marks == {2023: "missing", 2024: "none", 2025: "to_come"}
    assert _row(store, f"{W2}|1099_int").cells[2024].mark == "na"
    assert _row(store, f"{W2}|w2").cells[2025].symbol == ("here", "Here; its box couldn't be read")
    later = next(r for r in matrix.rows(store, date(2026, 10, 16)) if r.source.id == source)
    assert later.cells[2025].mark == "missing"  # the extended deadline passed


def test_a_big_change_is_marked() -> None:
    def big(before: str | None, now: str | None) -> str:
        to = lambda v: matrix.Cell([Decimal(v) if v is not None else None])  # noqa: E731
        return matrix.Row(None, "w2", ["1"], "W-2", "", {2024: to(before), 2025: to(now)}).big(2025)

    assert big("2000", "2600") == "Up 30% ($600) vs 2024"  # 25% and $500
    assert big("1000", "1300") == ""  # 30%, but $300
    assert big("10000", "12000") == ""  # $2,000, but 20%
    assert big("2640", "-1180") == "From income to a loss vs 2024"  # a change of sign
    assert big("3402", "1866") == "Down 45% ($1,536) vs 2024"
    assert big("0", "800") == "Up ($800) vs 2024"
    assert big(None, "800") == ""


def test_a_scanned_amount_is_marked_to_check(store: RecordStore) -> None:
    doc = store.get("expectation", f"2025/{W2}.w2")[0].evidence[0].doc
    with store.operation("test", "read from a scan"):
        store.put(FigureSet(year=2025, suggested=[Suggestion(
            source=W2, doc_type="w2", doc=doc, values=[Decimal(5), None, None, None],
            boxes=["1", "2", "16", "17"], scanned=True)]),
            None)  # fmt: skip
    row = _row(store, f"{W2}|w2")
    assert row.cells[2025].read and row.cells[2025].scanned
    assert "a scan, so check it against the paper" in row.tip(2025)
    stale = Suggestion(source=W2, doc_type="w2", doc=doc, values=[Decimal(5)], boxes=["2"])
    fs, rev = store.get("figures", "2025")
    with store.operation("test", "read for another box"):
        store.put(fs.model_copy(update={"suggested": [stale]}), rev)
    assert _row(store, f"{W2}|w2").cells[2025].amount is None  # never shown under box 1


def test_1099_div_w2_box_12_and_tax_exempt_interest_are_read(store: RecordStore) -> None:
    div = _sample("1099_div", 2024, "quarry-hill-funds")
    assert classify("Form 1099-DIV  Dividends and Distributions  2025", "a.pdf", [], 2026).doc_type \
        == "1099_div"  # fmt: skip
    consolidated = "2025 Consolidated Tax Statement  Form 1099-DIV  Form 1099-B"
    assert classify(consolidated, "b.pdf", [], 2026).doc_type == "1099_composite"
    # a dividend payer's row takes a 1099-DIV on its own
    _add(store, "quarry-hill-funds.joint", "1099_composite", 2024, div)
    exp = store.get("expectation", "2024/quarry-hill-funds.joint.1099-composite")[0]
    doc = store.get("document", exp.evidence[0].doc)[0]
    assert doc.classification.doc_type == "1099_div" and exp.evidence[0].role != "other"
    want = next(d for d in DOCS if (d.doc_type, d.year, d.layout) == ("1099_div", 2024, "tuned"))
    got = read_boxes(div, "1099_div", ["1a", "1b"])
    assert {b: r.value for b, r in got.items()} == {b: want.figures[b] for b in ("1a", "1b")}
    w2 = read_boxes(_sample("w2", 2024, "brightwater-analytics"), "w2", ["12a"])
    assert w2["12a"].value == 6500
    interest = read_boxes(_sample("1099_int", 2025, "tamarack-savings-bank"), "1099_int", ["8"])
    assert interest["8"].value == 300


def test_the_page(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/years").text  # Across the years
    assert "Across the years" in html and "(usual)" in html and 'class="bx"' in html
    assert "big change" in html and "expected, never arrived" in html
    matrix.read_pdfs(state.store, "test")  # as when the documents arrive
