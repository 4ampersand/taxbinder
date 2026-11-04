"""A fiscal-year K-1 is filed under the year its tax year ends, and marked."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from pathlib import Path

from helpers import new_vault
from pdfgen import make_pdf
from samples.generate import _render, plan
from samples.household import FISCAL

from taxbinder.ingest.classify import classify
from taxbinder.ingest.inbox import ingest_file
from taxbinder.ingest.pdftext import pdf_info
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

HEAD = "Schedule K-1 (Form 1065) 2023\nPartner's Share of Income, Deductions, Credits, etc."


def read(*lines: str):  # noqa: ANN201
    return classify(pdf_info(make_pdf(list(lines)))[1], "k1.pdf", [], 2026)


def test_filed_by_the_year_its_tax_year_ends() -> None:
    c = read(*HEAD.split("\n"), "For calendar year 2023, or tax year beginning 07/01/2023 "
             "ending 06/30/2024")  # fmt: skip
    assert (c.doc_type, c.tax_year, c.fiscal_year_end) == ("k1_1065", 2024, date(2024, 6, 30))
    assert c.confidence <= 0.75  # looked at on review
    blank = read(*HEAD.split("\n"), "For calendar year 2023, or tax year beginning ______ "
                 "ending ______")  # fmt: skip
    assert (blank.tax_year, blank.fiscal_year_end) == (2023, None)
    calendar = read(*HEAD.split("\n"), "tax year beginning 01/01/2023 ending 12/31/2023")
    assert (calendar.tax_year, calendar.fiscal_year_end) == (2023, None)
    statement = read("Form 1099-INT 2023 Interest Income",
                     "Statement period: tax year beginning 07/01/2023 ending 06/30/2024")  # fmt: skip
    assert (statement.tax_year, statement.fiscal_year_end) == (2023, None)  # not a K-1


def test_the_sample_fiscal_k1s_and_the_documents_page(tmp_path: Path) -> None:
    from test_web import client

    k1s = [d for d in plan(random.Random(7)) if d.issuer == FISCAL]
    store = RecordStore(new_vault(tmp_path / "vault"))
    for i, d in enumerate(k1s):
        path = store.vault.inbox / f"k1-{i}.pdf"
        path.write_bytes(_render(d, random.Random(i)))
        doc = ingest_file(store, path, "upload", "test", now=datetime(2026, 3, 2, tzinfo=UTC))
        c = store.get("document", doc.doc_id)[0].classification
        assert c.tax_year == d.year and c.fiscal_year_end == date(d.year, 6, 30)
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    assert "fiscal year to 06/2024" in client(state).get("/documents?year=2024").text
    state.close_all()
    store.close()
