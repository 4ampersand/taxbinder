"""A new household's whole app: a New York family, last year's 1040, this
year's documents, no spreadsheet and no earlier vault. No false findings, no state
the household doesn't live in, and an unchecked year said to be unchecked."""

from __future__ import annotations

import random
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from samples.generate import _render, plan, returns

from taxbinder.claims import add_payment
from taxbinder.household import confirm, worked_out
from taxbinder.ingest.inbox import ingest_file
from taxbinder.returns import add_listed
from taxbinder.rules.reconcile import reconcile
from taxbinder.states import table
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

TODAY = date(2026, 3, 2)
PAGES = ["/", "/payments", "/donations", "/years", "/documents", "/history",
         "/privacy", "/backup", "/sharing", "/settings"]  # fmt: skip
# Every other state, by name: nothing in the app assumes where a household lives
ELSEWHERE = re.compile(
    "|".join(rf"\b{s.name}\b" for s in table().values() if s.name != "New York")
)


@pytest.fixture(scope="module")
def household(tmp_path_factory: pytest.TempPathFactory):
    from helpers import new_vault

    store = RecordStore(new_vault(tmp_path_factory.mktemp("new") / "vault"))
    docs = plan(random.Random(7))

    def drop(name: str, data: bytes, when: datetime) -> str:
        path = store.vault.inbox / name
        path.write_bytes(data)
        return ingest_file(store, path, "upload", "test", now=when).doc_id

    ret = drop("2024 return.pdf", returns(docs)[0][0], datetime(2026, 2, 1, tzinfo=UTC))
    confirm(store, worked_out(store, 2025), 2025, "test")  # MFJ, New York, from the 1040
    add_listed(store, ret, 2025, "test")
    for i, d in enumerate(x for x in docs if x.year == 2025 and x.filer == "household"):
        drop(f"doc-{i}.pdf", _render(d, random.Random(i)), datetime(2026, 3, 1, tzinfo=UTC))
    for j, amount, day, kind in [("federal", 4000, date(2025, 4, 14), "estimated_q1"),
                                 ("NY", 900, date(2025, 4, 14), "estimated_q1")]:  # fmt: skip
        add_payment(store, j, Decimal(amount), day, kind, "test")
    yield store
    store.close()


def test_no_false_findings(household: RecordStore) -> None:
    findings = reconcile(household, 2025, "test", today=TODAY).findings.findings
    off = [f for f in findings if f.rule == "R-SRC-03"]
    assert off, "documents that aren't on the checklist are still pointed out"
    for f in off:
        assert "isn't on the checklist yet" in f.message
        assert not re.search(r"Benefit|Donation receipt|Property tax|Tax return", f.message), f
    assert not [f for f in findings if ELSEWHERE.search(f.message)]
    ny = [f for f in findings if f.rule == "R-PAY-03" and f.id.endswith("NY.q2")]
    assert ny and "New York's dates aren't reviewed yet" in ny[0].message


def test_one_finding_for_a_year_with_no_checklist(tmp_path: Path) -> None:
    from helpers import new_vault

    store = RecordStore(new_vault(tmp_path / "vault"))
    for i, d in enumerate(
        x for x in plan(random.Random(7)) if x.year == 2025 and x.doc_type == "w2"
    ):
        path = store.vault.inbox / f"w2-{i}.pdf"
        path.write_bytes(_render(d, random.Random(i)))
        ingest_file(store, path, "upload", "test")
    [f] = [f for f in reconcile(store, 2025, "test", today=TODAY).findings.findings
           if f.rule == "R-SRC-03"]  # fmt: skip
    assert re.fullmatch(r"\d+ documents for 2025 aren't on a checklist yet", f.message)
    store.close()


def test_every_page_and_an_unchecked_year(household: RecordStore) -> None:
    from test_web import ORIGIN, client

    state = AppState(
        vault_path=household.vault.root, store=household, port=8765, emit=lambda e: None,
        today=lambda: TODAY,
    )  # fmt: skip
    c = client(state)
    for path in PAGES:
        for year in (2025, 2024):
            r = c.get(f"{path}?year={year}")
            assert r.status_code == 200, (path, year)
            text = re.sub(r"<script.*?</script>|<select.*?</select>|<datalist.*?</datalist>|<[^>]+>",
                          " ", r.text, flags=re.S)  # fmt: skip
            assert not ELSEWHERE.search(text), (path, year, ELSEWHERE.search(text).group(0))
    c.post("/api/year/new", json={"year": 2024, "from": 2025}, headers=ORIGIN)  # a card for it
    old = c.get("/?year=2024").text
    assert "TaxBinder doesn't check 2024: checks start with 2025" in old
    assert 'class="link recheck" data-year="2024"' not in old and "Open findings" not in old
    assert 'class="link recheck" data-year="2025"' in old  # beside 2025's card
    pay = c.get("/payments?year=2025").text
    assert '<option value="NY">' in pay and "data-ca1" not in pay
