"""Payments (tax_payment claims), shared by the CLI and the app: add, delete, list for display."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from taxbinder.models import CLAIM_ADAPTER
from taxbinder.store import RecordNotFound, RecordStore

PAYMENT_KINDS = {
    "estimated_q1": "Q1 estimate",
    "estimated_q2": "Q2 estimate",
    "estimated_q3": "Q3 estimate",
    "estimated_q4": "Q4 estimate",
    "extension": "Extension payment",
    "balance_due": "Balance due",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def free_claim_id(store: RecordStore, candidate: str) -> str:
    n, claim_id = 2, candidate
    while True:
        try:
            store.get("claim", claim_id)
        except RecordNotFound:
            return claim_id
        claim_id, n = f"{candidate}-{n}", n + 1


def payment_year(day: date, kind: str) -> int:
    """Q4, extension and balance payments made by Apr 15 belong to the year before."""
    early = day <= date(day.year, 4, 15) and kind != "estimated_q1"
    return day.year - 1 if early else day.year


def add_payment(
    store: RecordStore,
    jurisdiction: str,
    amount: Decimal,
    day: date,
    kind: str,
    actor: str,
    year: int | None = None,
) -> str:
    """Record an estimated, extension or balance-due payment; returns its claim id."""
    jurisdiction = jurisdiction.strip()
    if not jurisdiction:
        raise ValueError("jurisdiction is required (federal, CA, …)")
    if kind not in PAYMENT_KINDS:
        raise ValueError(f"unknown payment kind {kind!r}")
    if amount is None or amount <= 0:
        raise ValueError("the amount must be more than zero")
    year = year or payment_year(day, kind)
    prefix = "fed" if jurisdiction.lower() == "federal" else _slug(jurisdiction)
    claim = CLAIM_ADAPTER.validate_python(
        {
            "id": free_claim_id(store, f"{year}/payments/{prefix}.{day}"),
            "year": year,
            "date": day,
            "amount": amount,
            "entered_by": actor,
            "entered_at": datetime.now(UTC),
            "provenance": "hand_entry",
            "category": "tax_payment",
            "jurisdiction": "federal" if jurisdiction.lower() == "federal" else jurisdiction,
            "payment": {"kind": kind, "applied_to_year": year},
        }
    )
    with store.operation(actor, f"claim add {claim.id}"):
        store.put(claim, None)
    return claim.id


def delete_claim(store: RecordStore, claim_id: str, actor: str) -> None:
    """Delete a claim (a payment, a gift). Its files stay in the vault; Undo brings it back."""
    claim, rev = store.get("claim", claim_id)
    with store.operation(actor, f"claim delete {claim_id}"):
        store.delete("claim", claim_id, rev)


@dataclass
class PaymentRow:
    c: Any
    jurisdiction: str
    kind: str
    confirmation: tuple[str, str, str]  # key, label, pill class
    files: list[str]


PAYMENT_CATEGORIES = ("tax_payment", "property_tax", "vehicle_fee", "bill")


def paid_to(c: Any) -> str:  # noqa: ANN401
    """Who a payment went (or goes) to."""
    if c.category == "tax_payment":
        return "Federal" if c.jurisdiction == "federal" else c.jurisdiction
    if c.plan is not None and c.plan.payee:
        return c.plan.payee
    if c.category == "property_tax":
        return c.parcel.county or "Property tax"
    if c.category == "vehicle_fee":
        return c.vehicle.description
    return "Bill"


def what_for(c: Any) -> str:  # noqa: ANN401
    if c.category == "tax_payment":
        return PAYMENT_KINDS.get(c.payment.kind, "Payment")
    detail = c.plan.label if c.plan is not None else ""
    if c.category == "bill":  # "Other": the person's own description
        return detail or "Payment"
    label = {"property_tax": "Property tax", "vehicle_fee": "DMV fees"}.get(c.category, "Payment")
    return f"{label} · {detail}" if detail else label


def payment_row(c: Any) -> PaymentRow:  # noqa: ANN401
    proofs = [link.doc for link in c.evidence if link.role not in ("superseded", "bill")]
    req = next((r for r in c.requirements if r.id == "payment_confirmation"), None)
    if (req is not None and req.satisfied_by) or (req is None and proofs):
        confirmation = ("have", "Confirmation in the vault", "ok")
    elif req is not None and req.waived:
        confirmation = ("waived", "Waived", "dim")
    else:
        confirmation = ("missing", "Needs a confirmation", "warn")
    return PaymentRow(
        c=c,
        jurisdiction=paid_to(c),
        kind=what_for(c),
        confirmation=confirmation,
        files=[link.doc for link in c.evidence if link.role != "superseded"],
    )


def payment_rows(store: RecordStore, year: int) -> tuple[list[PaymentRow], dict[str, Decimal]]:
    """The year's payments made (oldest first) and the total per payee."""
    rows, totals = [], defaultdict(Decimal)
    for c in store.list("claim", year):
        if c.category not in PAYMENT_CATEGORIES or c.unpaid or c.amount is None:
            continue
        row = payment_row(c)
        rows.append(row)
        totals[row.jurisdiction] += c.amount
    rows.sort(key=lambda r: (r.c.date or r.c.plan.due, r.jurisdiction))
    return rows, dict(sorted(totals.items(), key=lambda kv: (kv[0] != "Federal", kv[0])))


def edit_payment(
    store: RecordStore,
    claim_id: str,
    actor: str,
    day: date | None = None,
    amount: Decimal | None = None,
    payee: str | None = None,
    label: str | None = None,
) -> str:
    """Change a recorded payment's date, amount, payee or description (files are kept). A
    non-tax payment dated in another year moves there. Returns its (possibly new) id."""
    claim, rev = store.get("claim", claim_id)
    if claim.category not in PAYMENT_CATEGORIES:
        raise ValueError("only payments can be edited here")
    if amount is not None and amount <= 0:
        raise ValueError("the amount must be more than zero")
    data = claim.model_dump()
    if day is not None:
        data["date"] = day
        if claim.plan is not None and claim.plan.state == "paid":
            data["plan"]["due"] = day
    if amount is not None:
        data["amount"] = amount
    if claim.plan is not None:
        if payee is not None and payee.strip():
            data["plan"]["payee"] = payee.strip()
        if label is not None:
            data["plan"]["label"] = label.strip()
    moved = day is not None and claim.category != "tax_payment" and day.year != claim.year
    if moved:
        rest = claim.id.split("/", 1)[1]
        data |= {"id": free_claim_id(store, f"{day.year}/{rest}"), "year": day.year}
    updated = CLAIM_ADAPTER.validate_python(data)
    if updated == claim:
        return claim_id
    with store.operation(actor, f"payment edit {claim_id}"):
        if moved:
            store.put(updated, None)
            store.delete("claim", claim_id, rev)
        else:
            store.put(updated, rev)
    return updated.id
