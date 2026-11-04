"""Consistency: R-CON-01…03."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from itertools import combinations

from taxbinder.rules.engine import Ctx, Flag, rule

# Documents that are issued during the tax year, so an in-year date is plausible.
IN_YEAR_TYPES = {
    "donation_ack",
    "form_8283_support",
    "payment_confirmation",
    "property_tax_bill",
    "vehicle_registration",
    "account_change_notice",
    "foreign_bank_statement",
}


@rule("R-CON-01", "Hand-entered values disagree", "warning", "possible_inconsistency")
def assertions_disagree(ctx: Ctx) -> Iterator[Flag]:
    """Non-donation claims (donations: R-DON-10)."""
    tolerance = Decimal(str(ctx.param("R-CON-01", "tolerance")))
    for c in ctx.claims:
        if c.category == "donation" or c.resolved is not None or len(c.assertions) < 2:
            continue
        values = [a.value for a in c.assertions]
        if max(values) - min(values) > tolerance:
            listed = "; ".join(f"{a.value:,} ({a.source})" for a in c.assertions)
            yield Flag([c.id], f"{c.id}: values disagree: {listed}")


@rule("R-CON-02", "Date outside the plausible window", "warning", "possible_inconsistency")
def implausible_dates(ctx: Ctx) -> Iterator[Flag]:
    next_year = date(ctx.year + 1, 1, 1)
    for e in ctx.expectations:
        if e.doc_type in IN_YEAR_TYPES:
            continue
        for ev in e.events:
            day = ev.at.date() if hasattr(ev.at, "hour") else ev.at
            if ev.type == "received" and day < next_year:
                yield Flag(
                    [e.id],
                    f"{e.id}: {ctx.year} {e.doc_type} marked received on {day}, before the tax "
                    "year ended (typo for the following year?)",
                    key=str(day),
                )
    for c in ctx.claims_of("donation"):
        if c.date.year != ctx.year:
            yield Flag([c.id], f"{c.id}: gift dated {c.date} is outside {ctx.year}")


@rule("R-CON-03", "Possible duplicate claims", "warning", "possible_inconsistency")
def duplicates(ctx: Ctx) -> Iterator[Flag]:
    days = ctx.param("R-CON-03", "days")

    def same(a, b) -> bool:  # noqa: ANN001
        if a.category != b.category or a.amount != b.amount or a.date is None or b.date is None:
            return False
        if abs((a.date - b.date).days) > days:
            return False
        if a.category == "donation":
            return a.payee.strip().lower() == b.payee.strip().lower()
        if a.category == "tax_payment":
            return a.jurisdiction == b.jurisdiction
        return False

    for a, b in combinations(ctx.claims, 2):
        if same(a, b):
            first, second = sorted([a.id, b.id])
            yield Flag(
                [first, second], f"{first} and {second} look like the same {a.category}", key=second
            )
