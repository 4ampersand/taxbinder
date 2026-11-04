"""Bills and scheduled payments, shared by the CLI and the app (§5.5, §11.2).

A bill (property tax, vehicle registration, an estimated tax payment, any other bill) becomes one
claim per installment with a `plan`: planned → scheduled (for a date) → paid. TaxBinder never
pays anything: it keeps the bill, the plan, the reminders and the proof.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from taxbinder.claims import PAYMENT_KINDS, free_claim_id, payment_year
from taxbinder.models import CLAIM_ADAPTER
from taxbinder.models.claim import PaymentPlan
from taxbinder.models.common import Link
from taxbinder.store import RecordStore

BILL_KINDS = {
    "tax_payment": "Estimated tax",
    "property_tax": "Property tax",
    "vehicle_fee": "Vehicle / DMV fees",
    "bill": "Other",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "bill"


def add_bill(
    store: RecordStore,
    kind: str,
    payee: str,
    installments: list[tuple[str, Decimal, date]],
    actor: str,
    bill_doc: str | None = None,
    jurisdiction: str = "",
    payment_kind: str = "",
    paid: bool = False,
) -> list[str]:
    """One planned payment per installment ((label, amount, due)); returns their claim ids.
    With `paid`, they are payments already made (the date is when it was paid)."""
    payee = payee.strip()
    if kind not in BILL_KINDS:
        raise ValueError(f"unknown kind of bill {kind!r}")
    if kind == "tax_payment":
        if not jurisdiction.strip():
            raise ValueError("say who the tax is paid to (federal, CA …)")
        if payment_kind not in PAYMENT_KINDS:
            raise ValueError(f"unknown payment kind {payment_kind!r}")
        payee = payee or ("IRS" if jurisdiction.lower() == "federal" else jurisdiction)
    if not payee:
        raise ValueError("say who the bill is paid to")
    if not installments:
        raise ValueError("add at least one installment")
    if bill_doc:
        store.get("document", bill_doc)  # RecordNotFound for an unknown document
    now = datetime.now(UTC)
    ids: list[str] = []
    verb = "payment" if paid else "bill"
    with store.operation(actor, f"{verb} {BILL_KINDS[kind].lower()}: {payee}"):
        for n, (label, amount, due) in enumerate(installments, start=1):
            if amount is None or amount <= 0:
                raise ValueError(f"installment {n}: the amount must be more than zero")
            data: dict[str, Any] = {
                "category": kind,
                "amount": amount,
                "entered_by": actor,
                "entered_at": now,
                "provenance": "hand_entry",
                "plan": PaymentPlan(
                    due=due, payee=payee, label=label or "", state="paid" if paid else "planned"
                ),
                "evidence": (
                    [Link(doc=bill_doc, role="bill", linked_at=now, linked_by=actor)]
                    if bill_doc
                    else []
                ),
            }
            if kind == "tax_payment":
                year = payment_year(due, payment_kind)
                prefix = "fed" if jurisdiction.lower() == "federal" else _slug(jurisdiction)
                data |= {
                    "id": f"{year}/payments/{prefix}.{due}",
                    "jurisdiction": "federal"
                    if jurisdiction.lower() == "federal"
                    else jurisdiction,
                    "payment": {"kind": payment_kind, "applied_to_year": year},
                    "date": due,
                }
            else:
                year = due.year  # deductible in the year it's paid; moved if paid in another year
                data["date"] = due
                if kind == "property_tax":
                    installment = (
                        {1: "first", 2: "second"}.get(n, "full")
                        if len(installments) > 1
                        else "full"
                    )
                    data |= {
                        "id": f"{year}/property-tax/{due}.{_slug(payee)}-{n}",
                        "parcel": {"county": payee, "installment": installment},
                    }
                elif kind == "vehicle_fee":
                    data |= {
                        "id": f"{year}/vehicle-fees/{due}.{_slug(payee)}",
                        "vehicle": {"description": payee},
                    }
                else:
                    data["id"] = f"{year}/bills/{due}.{_slug(payee)}"
            data["year"] = year
            data["id"] = free_claim_id(store, data["id"])
            claim = CLAIM_ADAPTER.validate_python(data)
            store.put(claim, None)
            ids.append(claim.id)
    return ids


def _planned(store: RecordStore, claim_id: str) -> tuple[Any, str]:
    claim, rev = store.get("claim", claim_id)
    if claim.plan is None:
        raise ValueError(f"{claim_id} is a recorded payment, not a planned one")
    return claim, rev


def schedule(store: RecordStore, claim_id: str, pay_on: date, note: str, actor: str) -> None:
    claim, rev = _planned(store, claim_id)
    if claim.plan.state == "paid":
        raise ValueError("it's already paid")
    plan = claim.plan.model_copy(
        update={"state": "scheduled", "pay_on": pay_on, "note": note.strip() or claim.plan.note}
    )
    with store.operation(actor, f"schedule {claim_id} for {pay_on}"):
        store.put(claim.model_copy(update={"plan": plan, "date": pay_on}), rev)


def mark_paid(
    store: RecordStore, claim_id: str, paid_on: date, actor: str, amount: Decimal | None = None
) -> str:
    """Mark a planned payment paid; returns its id (which changes when a bill paid in another
    calendar year moves to that year: property tax is deductible in the year it's paid)."""
    claim, rev = _planned(store, claim_id)
    plan = claim.plan.model_copy(update={"state": "paid"})
    update: dict[str, Any] = {"plan": plan, "date": paid_on}
    if amount is not None:
        if amount <= 0:
            raise ValueError("the amount must be more than zero")
        update["amount"] = amount
    moved = claim.category != "tax_payment" and paid_on.year != claim.year
    with store.operation(actor, f"paid {claim_id} on {paid_on}"):
        if not moved:
            store.put(claim.model_copy(update=update), rev)
            return claim_id
        prefix, rest = claim.id.split("/", 1)
        new_id = free_claim_id(store, f"{paid_on.year}/{rest}")
        store.put(
            CLAIM_ADAPTER.validate_python(
                {**claim.model_dump(), **update, "id": new_id, "year": paid_on.year}
            ),
            None,
        )
        store.delete("claim", claim_id, rev)
        return new_id


def upcoming(store: RecordStore) -> list[Any]:
    """Every planned or scheduled payment, all years, soonest first."""
    return sorted(
        (c for c in store.list("claim") if c.unpaid),
        key=lambda c: (c.plan.pay_on or c.plan.due, c.id),
    )


def due_soon(store: RecordStore, today: date, days: int = 14) -> list[Any]:
    return [c for c in upcoming(store) if c.plan.due <= today + timedelta(days=days)]


def calendar_event(claim: Any, now: datetime | None = None) -> str:  # noqa: ANN401
    """An iCalendar event on the pay-on date (else the due date), alerts 7 days and 1 day before."""
    now = now or datetime.now(UTC)
    day = claim.plan.pay_on or claim.plan.due
    amount = f"${claim.amount:,.2f}" if claim.amount is not None else ""
    what = BILL_KINDS.get(claim.category, "Payment")
    title = " ".join(
        x
        for x in [
            f"Pay {claim.plan.payee}",
            amount,
            f"({what}, {claim.plan.label})" if claim.plan.label else f"({what})",
        ]
        if x
    )
    desc = (
        f"Due {claim.plan.due}. Pay it at the payee's site or in your bank, then mark it paid "
        "in TaxBinder with the confirmation."
    )

    def esc(text: str) -> str:
        return (
            text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TaxBinder//Bills//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid5(uuid.NAMESPACE_URL, 'taxbinder:' + claim.id)}@taxbinder",
        f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{esc(title)}",
        f"DESCRIPTION:{esc(desc)}",
    ]
    for before, label in (("-P7D", "in a week"), ("-P1D", "tomorrow")):
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:{before}",
            f"DESCRIPTION:{esc(title)} {label}",
            "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
