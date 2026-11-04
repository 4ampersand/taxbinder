"""Bills and scheduled payments: planned → scheduled → paid, reminders, calendar, app, CLI."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pdfgen import make_pdf
from test_sharing import LAN_ORIGIN, local, pair, shared  # noqa: F401 - `shared` is a fixture
from test_web import ORIGIN, _upload, client, state  # noqa: F401 - `state` is a fixture
from test_years import store  # noqa: F401 - fixture

from taxbinder.bills import (
    add_bill,
    calendar_event,
    due_soon,
    mark_paid,
    schedule,
    upcoming,
)
from taxbinder.rules.reconcile import load_ctx, reconcile
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.web.app import AppState


def _property_tax(store: RecordStore) -> list[str]:  # noqa: F811
    return add_bill(
        store,
        "property_tax",
        "Hollis County",
        [
            ("1st installment", Decimal("6100.50"), date(2026, 12, 10)),
            ("2nd installment", Decimal("6100.50"), date(2027, 4, 12)),
        ],
        "t",
    )


def test_bill_lifecycle_and_checks(store: RecordStore) -> None:  # noqa: F811
    first, second = _property_tax(store)
    assert (first[:5], second[:5]) == ("2026/", "2027/")  # deductible in the year paid
    claim, _ = store.get("claim", first)
    assert (claim.plan.state, claim.plan.payee, claim.parcel.installment) == (
        "planned",
        "Hollis County",
        "first",
    )
    assert [c.id for c in upcoming(store)] == [first, second]

    ctx = load_ctx(store, 2026, date(2026, 12, 1))
    assert [c.id for c in ctx.planned] == [first] and first not in {c.id for c in ctx.claims}

    found = reconcile(store, 2026, "t", today=date(2026, 12, 1)).findings.findings
    soon = [f for f in found if f.rule == "R-BIL-02"]
    assert len(soon) == 1 and "not scheduled yet" in soon[0].message
    schedule(store, first, date(2026, 12, 5), "Chase bill pay", "t")
    found = reconcile(store, 2026, "t", today=date(2026, 12, 1)).findings.findings
    open_soon = [f for f in found if f.rule == "R-BIL-02" and f.status == "open"]
    assert len(open_soon) == 1 and "scheduled for 2026-12-05" in open_soon[0].message
    found = reconcile(store, 2026, "t", today=date(2026, 12, 11)).findings.findings
    assert any(
        f.rule == "R-BIL-01" and f.status == "open" and "isn't marked paid" in f.message
        for f in found
    )

    assert mark_paid(store, first, date(2026, 12, 5), "t") == first
    paid, _ = store.get("claim", first)
    assert (paid.plan.state, paid.date, paid.unpaid) == ("paid", date(2026, 12, 5), False)
    found = reconcile(store, 2026, "t", today=date(2026, 12, 11)).findings.findings
    assert not any(f.rule.startswith("R-BIL") and f.status == "open" for f in found)
    with pytest.raises(ValueError, match="already paid"):
        schedule(store, first, date(2026, 12, 6), "", "t")

    # the 2nd installment paid early, in December: it moves to 2026
    moved = mark_paid(store, second, date(2026, 12, 20), "t", Decimal("6000"))
    assert moved.startswith("2026/property-tax/") and moved != second
    with pytest.raises(RecordNotFound):
        store.get("claim", second)
    assert store.get("claim", moved)[0].amount == Decimal("6000")
    assert due_soon(store, date(2026, 12, 1)) == []


def test_planned_tax_payment_is_not_a_payment_until_paid(store: RecordStore) -> None:  # noqa: F811
    from taxbinder.web.views import tiles

    before = tiles(store, 2025)["payments_total"]
    [q4] = add_bill(
        store,
        "tax_payment",
        "",
        [("", Decimal("40000"), date(2026, 1, 15))],
        "t",
        jurisdiction="federal",
        payment_kind="estimated_q4",
    )
    assert q4.startswith("2025/payments/fed.2026-01-15")  # Q4 paid in January → prior year
    assert tiles(store, 2025)["payments_total"] == before
    assert q4 not in {c.id for c in load_ctx(store, 2025, date(2026, 1, 1)).claims}
    mark_paid(store, q4, date(2026, 1, 12), "t")
    assert tiles(store, 2025)["payments_total"] == before + 40000
    with pytest.raises(ValueError, match="who the tax is paid to"):
        add_bill(
            store,
            "tax_payment",
            "",
            [("", Decimal(1), date(2026, 1, 15))],
            "t",
            payment_kind="estimated_q4",
        )
    with pytest.raises(ValueError, match="more than zero"):
        add_bill(store, "bill", "HOA", [("", Decimal(0), date(2026, 1, 15))], "t")
    with pytest.raises(ValueError, match="recorded payment"):
        recorded = next(
            c.id
            for c in store.list("claim", 2025)
            if c.category == "tax_payment" and c.plan is None
        )
        schedule(store, recorded, date(2026, 1, 1), "", "t")


def test_calendar_event(store: RecordStore) -> None:  # noqa: F811
    first, _ = _property_tax(store)
    schedule(store, first, date(2026, 12, 5), "Chase, autopay", "t")
    ics = calendar_event(store.get("claim", first)[0])
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20261205" in ics and "DTEND;VALUE=DATE:20261206" in ics
    assert "SUMMARY:Pay Hollis County $6\\,100.50 (Property tax\\, 1st installment)" in ics
    assert ics.count("BEGIN:VALARM") == 2 and "TRIGGER:-P7D" in ics and "TRIGGER:-P1D" in ics


def test_bills_in_the_app(shared: AppState, tmp_path: Path) -> None:  # noqa: F811
    opened: list[Path] = []
    shared.open_file = opened.append
    shared.downloads_dir = tmp_path / "Downloads"
    c = local(shared)
    html = c.get("/payments").text
    assert 'id="add-pay-open"' in html and 'data-ca1="' not in html and "Nothing to pay" in html
    doc = _upload(
        c, make_pdf(["Secured Property Tax Bill 2026-27", "Hollis County"]), "bill.pdf"
    ).json()["doc_id"]
    r = c.post(
        "/api/bills",
        json={
            "kind": "property_tax",
            "payee": "Hollis County",
            "doc_id": doc,
            "installments": [
                {"label": "1st installment", "due": "2026-12-10", "amount": "$6,100.50"},
                {"label": "2nd installment", "due": "2027-04-12", "amount": "6100.50"},
            ],
        },
        headers=ORIGIN,
    )
    assert r.status_code == 200, r.text
    first, second = r.json()["ids"]
    for bad in (
        {"kind": "property_tax", "payee": "X", "installments": [{"due": "", "amount": "1"}]},
        {
            "kind": "property_tax",
            "payee": "X",
            "installments": [{"due": "2026-12-10", "amount": ""}],
        },
        {"kind": "nope", "payee": "X", "installments": [{"due": "2026-12-10", "amount": "1"}]},
    ):
        assert c.post("/api/bills", json=bad, headers=ORIGIN).status_code == 400, bad
    html = c.get("/payments").text
    upcoming_part = html.split("Upcoming")[1].split("Paid for")[0]
    assert "Hollis County" in upcoming_part and "Property tax · 1st installment" in upcoming_part
    assert "Download bill" in upcoming_part and "Planned" in upcoming_part

    r = c.post(
        "/api/bills/schedule",
        json={"id": first, "pay_on": "2026-12-05", "note": "Chase"},
        headers=ORIGIN,
    )
    assert r.status_code == 200
    r = c.post("/api/bills/calendar", json={"id": first}, headers=ORIGIN)
    assert (
        r.status_code == 200
        and opened
        and opened[0].suffix == ".ics"
        and opened[0].parent == shared.downloads_dir
    )
    assert "BEGIN:VEVENT" in opened[0].read_text()
    assert ">1 due</span>" not in c.get("/").text  # not due within 14 days of the test's today

    sam = pair(shared)
    ics = sam.get("/payments/calendar", params={"id": first})
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert ics.headers["content-disposition"].startswith("attachment;")
    assert (
        sam.post("/api/bills/calendar", json={"id": first}, headers=LAN_ORIGIN).status_code == 403
    )

    r = c.post(
        "/api/bills/paid", json={"id": first, "paid_on": "2026-12-05", "amount": ""}, headers=ORIGIN
    )
    assert r.status_code == 200 and r.json()["id"] == first
    up = _upload(
        c, make_pdf(["Payment confirmation", "Hollis County"]), "receipt.pdf", target=first
    )
    assert first in up.json()["linked_to"]
    gone = c.post("/api/payments/delete", json={"id": first}, headers=ORIGIN)
    assert gone.status_code == 200  # proof attached: the file stays in the vault
    assert (
        c.post("/api/undo", json={"sha": gone.json()["undo"]["sha"]}, headers=ORIGIN).status_code
        == 200
    )
    assert (
        c.post("/api/payments/delete", json={"id": second}, headers=ORIGIN).status_code == 200
    )  # only the bill
    for path in ("/api/bills", "/api/bills/schedule", "/api/bills/paid", "/api/bills/calendar"):
        assert client(shared, login=False).post(path, json={}, headers=ORIGIN).status_code == 401


def test_bill_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    cli("init")
    out = cli(
        "bill",
        "add",
        "Hollis County",
        "--kind",
        "property_tax",
        "--installment",
        "6100.50@2026-12-10:1st installment",
        "--installment",
        "6100.50@2027-04-12:2nd installment",
    )
    first = out.split()[1]
    assert first.startswith("2026/property-tax/") and "planned 2027/" in out
    assert "due 2026-12-10" in cli("bill", "list")
    assert "scheduled" in cli("bill", "schedule", first, "--on", "2026-12-05", "--note", "Chase")
    assert f"paid {first}" in cli("bill", "paid", first, "--on", "2026-12-05")
    assert "2026-12-10" not in cli("bill", "list")
    assert "isn't a date" in cli("bill", "paid", first, "--on", "soon", ok=False)


def test_record_property_tax_dmv_and_other_payments_made(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/payments").text
    assert (
        '<option value="vehicle_fee">Vehicle / DMV fees</option>' in html
        and 'id="ap-later"' in html
    )
    made = [
        (
            {
                "category": "property_tax",
                "payee": "Hollis County",
                "label": "2nd installment",
                "date": "2025-04-08",
                "amount": "6,100.50",
            },
            "2025/property-tax/2025-04-08.hollis-county-1",
            "Property tax · 2nd installment",
        ),
        (
            {
                "category": "vehicle_fee",
                "payee": "State DMV",
                "label": "registration, SUV",
                "date": "2025-07-02",
                "amount": "412",
            },
            "2025/vehicle-fees/2025-07-02.state-dmv",
            "DMV fees · registration, SUV",
        ),
        (
            {
                "category": "bill",
                "payee": "Brookside HOA",
                "label": "Q3 dues",
                "date": "2025-07-05",
                "amount": "300",
            },
            "2025/bills/2025-07-05.brookside-hoa",
            "Q3 dues",
        ),
    ]
    for body, claim_id, shown in made:
        r = c.post("/api/payments", json=body, headers=ORIGIN)
        assert r.status_code == 200 and r.json()["id"] == claim_id, r.text
        claim, _ = state.store.get("claim", claim_id)
        assert (
            claim.plan.state == "paid"
            and not claim.unpaid
            and claim.date.isoformat() == body["date"]
        )
        paid_part = c.get("/payments").text.split('data-table="payments"')[1]
        row = paid_part.split(f'data-id="{claim_id}"')[1].split("</tr>")[0]
        assert shown in row and body["payee"] in row
    assert 'Upcoming <span class="muted">0</span>' in c.get("/payments").text  # none planned
    r = c.post(
        "/api/payments",
        json={"category": "vehicle_fee", "payee": "DMV", "date": "2025-07-02", "amount": ""},
        headers=ORIGIN,
    )
    assert r.status_code == 400 and "amount" in r.json()["error"]
    assert (
        c.post(
            "/api/undo",
            json={
                "sha": c.post(
                    "/api/payments", json={**made[2][0], "date": "2025-08-05"}, headers=ORIGIN
                ).json()["undo"]["sha"]
            },
            headers=ORIGIN,
        ).status_code
        == 200
    )
