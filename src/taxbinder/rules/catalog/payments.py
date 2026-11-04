"""Tax payments: R-PAY-01…05."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from taxbinder.rules.engine import Ctx, Flag, Require, rule


def _by_jurisdiction(ctx: Ctx) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for c in ctx.claims_of("tax_payment"):
        if c.payment.applied_to_year == ctx.year:
            out[c.jurisdiction].append(c)
    return out


def _key(jurisdiction: str) -> str:
    return jurisdiction if jurisdiction == "federal" else jurisdiction.upper()


@rule("R-PAY-01", "Payments don't add up to the summary figure", "error", "possible_inconsistency")
def summary_mismatch(ctx: Ctx) -> Iterator[Flag]:
    tolerance = Decimal(str(ctx.param("R-PAY-01", "tolerance")))
    payments = {_key(j): cs for j, cs in _by_jurisdiction(ctx).items()}
    for s in ctx.year_config.summaries:
        if not s.subject.startswith("tax_payment/"):
            continue
        j = _key(s.subject.split("/", 1)[1])
        claims = payments.get(j, [])
        total = sum((c.amount for c in claims), Decimal(0))
        if abs(total - s.value) > tolerance:
            yield Flag(
                [str(ctx.year), *(c.id for c in claims)],
                f"{j} payments total ${total:,.2f} ({len(claims)} payments) but {s.source} "
                f"says ${s.value:,.2f}",
                key=j,
                suggested_action="Find the missing or extra payment, or correct the summary",
            )


@rule("R-PAY-02", "Each payment needs a confirmation", "error", "missing_evidence")
def confirmation(ctx: Ctx) -> Iterator[Require]:
    for c in ctx.claims_of("tax_payment"):
        yield Require(
            c,
            "payment_confirmation",
            ["payment_confirmation"],
            f"{c.jurisdiction} ${c.amount:,.2f} paid {c.date}: no confirmation or bank record",
        )


def _installment(day: date, due: list[date], quarters: list[int]) -> str:
    for when, q in zip(due, quarters, strict=True):
        if day <= when:
            return f"estimated_q{q}"
    return "extension"


@rule("R-PAY-03", "Installment timing: late or missing estimated payments", "warning", "change")
def installment_timing(ctx: Ctx) -> Iterator[Flag]:
    """Each jurisdiction the household paid, by its own dates: the reviewed ones in the year's
    parameters, else the state table's, said to be unreviewed in the finding."""
    from taxbinder.states import schedule, table

    for j, claims in _by_jurisdiction(ctx).items():
        j = _key(j)
        found = schedule(j, ctx.year, ctx.params)
        if found is None:
            continue  # no estimated payments there (or not a state the table knows)
        due, quarters = found.due, found.quarters
        state = table().get(j)
        penalty = "Form 2210" if j == "federal" else state.forms.get("penalty") if state else None
        exposure = f" ({penalty} exposure)" if penalty else ""
        unreviewed = (
            ""
            if found.reviewed
            else f" ({state.name if state else j}'s dates aren't reviewed yet: check them)"
        )
        estimated = [c for c in claims if c.payment.kind != "balance_due"]
        previous = date.min
        for when, q in zip(due, quarters, strict=True):
            if not any(previous < c.date <= when for c in estimated):
                yield Flag(
                    [str(ctx.year), *(c.id for c in claims)],
                    f"{j}: no payment for installment Q{q} due {when}; possible underpayment"
                    f"{exposure}{unreviewed}",
                    key=f"{j}.q{q}",
                )
            previous = when
        for c in claims:
            expected = _installment(c.date, due, quarters)
            if c.payment.kind not in (expected, "balance_due"):
                yield Flag(
                    [c.id],
                    f"{c.id}: recorded as {c.payment.kind}, but by {j} due dates it counts as "
                    f"{expected}{unreviewed}",
                    key="kind",
                )


@rule(
    "R-PAY-04", "Payment to a state with no known state income", "warning", "possible_inconsistency"
)
def state_without_income(ctx: Ctx) -> Iterator[Flag]:
    states = {s.upper() for s in ctx.year_config.resident_states}
    for src in ctx.sources:
        for p in src.produces:
            states |= {s.upper() for s in p.states or []}
    for j, claims in _by_jurisdiction(ctx).items():
        if j != "federal" and j.upper() not in states:
            yield Flag(
                [c.id for c in claims],
                f"{j} payments (${sum(c.amount for c in claims):,.2f}) but no {j} residency or "
                "state-source income is recorded",
                key=j,
                suggested_action="Record the income source (e.g. K-1 state) or residency",
            )


@rule("R-PAY-05", "Prior-year penalty noted: plan this year's payments", "info", "change")
def prior_penalty(ctx: Ctx) -> Iterator[Flag]:
    for c in ctx.claims_of("tax_payment"):
        if c.notes and "penalt" in c.notes.lower():
            yield Flag([c.id], f"{c.jurisdiction}: {c.notes}")


def _when(c: Any) -> date:  # noqa: ANN401
    return c.plan.pay_on or c.plan.due


def _what(c: Any) -> str:  # noqa: ANN401
    amount = f"${c.amount:,.2f}" if c.amount is not None else "a payment"
    return f"{c.plan.payee or c.category.replace('_', ' ')}: {amount}"


@rule("R-BIL-01", "Bill past its due date and not marked paid", "error", "missing_evidence")
def bill_overdue(ctx: Ctx) -> Iterator[Flag]:
    for c in ctx.planned:
        if c.plan.due < ctx.today:
            yield Flag(
                [c.id],
                f"{_what(c)} was due {c.plan.due} and isn't marked paid",
                suggested_action="Pay it, then mark it paid with the confirmation",
            )


@rule("R-BIL-02", "Bill due soon", "warning", "change")
def bill_due_soon(ctx: Ctx) -> Iterator[Flag]:
    days = ctx.params.values.get("R-BIL-02", {}).get("days", 14)  # default: two weeks
    for c in ctx.planned:
        if ctx.today <= c.plan.due <= ctx.today + timedelta(days=days):
            if c.plan.state == "scheduled" and c.plan.pay_on:
                text, action = f"scheduled for {c.plan.pay_on}", "Mark it paid once it goes through"
            else:
                text, action = "not scheduled yet", "Schedule it, or pay it and mark it paid"
            yield Flag(
                [c.id],
                f"{_what(c)} is due {c.plan.due} ({text})",
                key=c.plan.state,
                suggested_action=action,
            )
