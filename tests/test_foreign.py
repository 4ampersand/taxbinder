"""Foreign accounts: accounts abroad for the FBAR (fictional data: the Quills' three
euro accounts, tests/quills.py FOREIGN)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from pdfgen import make_pdf
from test_web import ORIGIN, _upload, client, state  # noqa: F401 - `state` is a fixture
from test_years import store  # noqa: F401 - fixture

from taxbinder import foreign
from taxbinder.package.build import build_package
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState


def test_accounts_and_each_persons_total(store: RecordStore) -> None:  # noqa: F811
    rows, people, line = foreign.accounts(store, 2025)
    assert line == Decimal(10000) and len(rows) == 3
    assert rows[0].usd == Decimal("16444.44")  # €14,800 at 0.9 per dollar
    totals = {p.name: (p.total, p.over) for p in people}
    assert all(over for _, over in totals.values()) and len(totals) == 2
    # a new bank, in dollars (no rate needed), and one without a rate: not counted, said so
    foreign.add_account(store, 2025, "Harbor Trust Bank", "Checking …0042", "primary", "usd",
                        Decimal("3000"), "t")  # fmt: skip
    foreign.add_account(store, 2025, "Harbor Trust Bank", "Deposit", "primary", "INR",
                        Decimal("500000"), "t")  # fmt: skip
    rows, people, _ = foreign.accounts(store, 2025)
    harbor = [r for r in rows if r.bank == "Harbor Trust Bank"]
    assert [(r.account, r.usd) for r in harbor] == [("Checking …0042", Decimal("3000")),
                                                    ("Deposit", None)]  # fmt: skip
    me = next(p for p in people if p.unknown)
    assert me.unknown == 1 and me.total == Decimal("19444.44")
    assert store.get("institution", "harbor-trust-bank")[0].kind == "bank"
    # a joint account counts in full for each owner, as the check (R-FOR-01) counts it
    foreign.add_account(store, 2025, "Harbor Trust Bank", "Joint", "household", "USD",
                        Decimal("1000"), "t")  # fmt: skip
    rows, people, _ = foreign.accounts(store, 2025)
    assert {p.name: p.total for p in people} == {"Avery Quill": Decimal("20444.44"),
                                                 "Jordan Quill": Decimal("17666.67")}  # fmt: skip
    assert next(r for r in rows if r.account == "Joint").whose == "Joint"
    with pytest.raises(ValueError, match="already on 2025"):
        foreign.add_account(store, 2025, "Harbor Trust Bank", "Deposit", "primary", "INR",
                            Decimal("1"), "t")  # fmt: skip
    with pytest.raises(ValueError, match="3 letters"):
        foreign.add_account(store, 2025, "B", "A", "primary", "euro", Decimal("1"), "t")
    with pytest.raises(ValueError, match="highest balance"):
        foreign.add_account(store, 2025, "B", "A", "primary", "EUR", None, "t")


def test_the_page_add_edit_statement_delete(state: AppState, tmp_path: Path) -> None:  # noqa: F811
    c, s = client(state), state.store
    html = c.get("/foreign?year=2025").text
    assert 'href="/foreign"' in html and "an FBAR is needed" in html and "16,444" in html
    r = c.post("/api/foreign", json={"year": 2025, "bank": "Harbor Trust Bank",
               "account": "Rainy-day", "owner": "primary", "currency": "chf",
               "max_balance": "4,000", "interest": "12"}, headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text
    claim_id = r.json()["id"]
    assert "rate needed" in c.get("/foreign?year=2025").text
    r = c.post("/api/foreign", json={"what": "edit", "id": claim_id, "currency": "CHF",
               "max_balance": "4000", "rate": "0.8"}, headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200 and "5,000" in c.get("/foreign?year=2025").text
    # a statement attached to the account goes to the preparer in 08-Foreign
    pdf = make_pdf(["Harbor Trust Bank", "Account statement, 2025"])
    r = _upload(c, pdf, "statement.pdf", target=claim_id, year="2025",
                as_type="foreign_bank_statement")  # fmt: skip
    assert r.status_code == 200, r.text
    assert claim_id in r.json()["linked_to"]
    statement = s.get("document", r.json()["doc_id"])[0].classification.doc_type
    assert statement == "foreign_bank_statement"
    root = build_package(s, 2025, tmp_path / "pkg", "t").root
    assert any(p.suffix == ".pdf" for p in (root / "08-Foreign").iterdir())
    with (root / "08-Foreign" / "fbar-summary.csv").open() as f:
        summary = list(csv.DictReader(f))
    mine = next(x for x in summary if x["account"] == "Rainy-day")
    assert mine["bank"] == "Harbor Trust Bank" and mine["max_usd"] == "5000"
    # deleted: one change, undone with one Undo
    r = c.post("/api/foreign", json={"what": "delete", "id": claim_id}, headers=ORIGIN)
    assert r.status_code == 200 and "Rainy-day" not in c.get("/foreign?year=2025").text
    undone = c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN)
    assert undone.status_code == 200
    assert s.get("claim", claim_id)[0].foreign_account.rate == Decimal("0.8")
    bad = {"what": "edit", "id": "2025/donations/nope", "max_balance": "1"}
    assert c.post("/api/foreign", json=bad, headers=ORIGIN).status_code == 404


def test_last_years_accounts_are_offered_again(state: AppState) -> None:  # noqa: F811
    from taxbinder.years import new_year

    c, s = client(state), state.store
    new_year(s, 2026, 2025, "test")
    assert len(foreign.carry(s, 2026)) == 3
    html = c.get("/foreign?year=2026").text
    assert "On last year’s list" in html and html.count("act-fa-carry") == 3
    first = foreign.carry(s, 2026)[0]
    r = c.post("/api/foreign", json={"year": 2026, "source": first.c.foreign_account.source,
               "bank": first.bank, "account": first.account, "owner": first.c.taxpayer,
               "currency": "EUR", "max_balance": "9000"}, headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200, r.text
    assert len(foreign.carry(s, 2026)) == 2  # the same account: not offered again
    assert r.json()["id"] == f"2026/fbar/{first.c.foreign_account.source}"
