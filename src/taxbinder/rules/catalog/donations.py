"""Donations: R-DON-01…10."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal

from taxbinder.rules.engine import Ctx, Flag, Require, rule


def _money(v: Decimal) -> str:
    return f"${v:,.2f}"


@rule(
    "R-DON-01",
    "Cash donation at or above the threshold needs a written acknowledgment",
    "error",
    "missing_evidence",
)
def cash_ack(ctx: Ctx) -> Iterator[Require]:
    threshold = ctx.param("R-DON-01", "cash_ack_threshold")
    for c in ctx.claims_of("donation"):
        if c.donation.type == "cash" and not c.donation.to_daf and c.amount >= threshold:
            yield Require(
                c,
                "written_acknowledgment",
                ["donation_ack"],
                f"{c.payee} {_money(c.amount)} on {c.date}: no written acknowledgment "
                f"(required for cash gifts of ${threshold} or more)",
            )


@rule(
    "R-DON-02",
    "Stock or property gift over the threshold needs its transfer / gift records",
    "error",
    "missing_evidence",
)
def form_8283(ctx: Ctx) -> Iterator[Require]:
    threshold = ctx.param("R-DON-02", "form_8283_threshold")
    appraisal = ctx.param("R-DON-02", "appraisal_threshold")
    for c in ctx.claims_of("donation"):
        if c.donation.type not in ("securities", "property") or c.amount <= threshold:
            continue
        yield Require(
            c,
            "form_8283_section_a",
            ["form_8283_support"],
            f"{c.payee} {_money(c.amount)} "
            + (
                "stock gift: the broker's transfer record (with the cost basis) is missing"
                if c.donation.type == "securities"
                else "property gift: records of what was given and its value are missing"
            ),
        )
        # Publicly traded securities are exempt from the appraisal requirement.
        if c.donation.type == "property" and c.amount > appraisal:
            yield Require(
                c,
                "qualified_appraisal",
                ["form_8283_support"],
                f"{c.payee} {_money(c.amount)}: a qualified appraisal is needed for property "
                "over this amount",
            )


@rule(
    "R-DON-03",
    "Contribution to a DAF needs the sponsor's acknowledgment",
    "error",
    "missing_evidence",
)
def daf_ack(ctx: Ctx) -> Iterator[Require]:
    for c in ctx.claims_of("donation"):
        if c.donation.to_daf:
            yield Require(
                c,
                "written_acknowledgment_daf",
                ["donation_ack"],
                f"{c.payee} {_money(c.amount)} DAF contribution: no acknowledgment from the "
                "sponsoring organization",
            )


def _disagreeing(c, tolerance: Decimal) -> bool:  # noqa: ANN001
    values = [a.value for a in c.assertions]
    return bool(values) and max(values) - min(values) > tolerance


@rule(
    "R-DON-04",
    "Claimed amount differs from the acknowledgment",
    "warning",
    "possible_inconsistency",
)
def amount_vs_ack(ctx: Ctx) -> Iterator[Flag]:
    tolerance = Decimal(str(ctx.param("R-DON-04", "tolerance")))
    for c in ctx.claims_of("donation"):
        for a in c.assertions:
            if a.kind in ("document", "extraction") and abs(a.value - c.amount) > tolerance:
                yield Flag(
                    [c.id],
                    f"{c.payee}: claimed {_money(c.amount)} but {a.source} shows {_money(a.value)}",
                    key=a.source,
                )


@rule(
    "R-DON-05",
    "Securities donation needs a brokerage transfer confirmation",
    "warning",
    "missing_evidence",
)
def transfer_confirmation(ctx: Ctx) -> Iterator[Require]:
    for c in ctx.claims_of("donation"):
        if c.donation.type == "securities":
            shares = f"{c.donation.shares} shares" if c.donation.shares else "securities"
            yield Require(
                c,
                "brokerage_transfer_confirmation",
                ["form_8283_support"],
                f"{c.payee}: no brokerage confirmation of the {shares} transferred on {c.date}",
            )


@rule(
    "R-DON-06", "Short-term securities: deduction limited to basis", "error", "demonstrated_error"
)
def short_term_securities(ctx: Ctx) -> Iterator[Flag]:
    days = ctx.param("R-DON-06", "long_term_days")
    for c in ctx.claims_of("donation"):
        d = c.donation
        if d.type != "securities" or d.acquired is None or (c.date - d.acquired).days > days:
            continue
        if d.cost_basis_per_share is not None and d.shares is not None:
            basis = d.cost_basis_per_share * d.shares
            if c.amount > basis:
                yield Flag(
                    [c.id],
                    f"{c.payee}: held {(c.date - d.acquired).days} days (short term); deduction is "
                    f"limited to basis {_money(basis)}, not FMV {_money(c.amount)}",
                )


@rule("R-DON-07", "Quid-pro-quo payment needs a disclosure statement", "info", "missing_evidence")
def quid_pro_quo(ctx: Ctx) -> Iterator[Flag]:
    threshold = ctx.param("R-DON-07", "quid_pro_quo_threshold")
    for c in ctx.claims_of("donation"):
        if c.donation.quid_pro_quo and c.amount > threshold:
            yield Flag(
                [c.id], f"{c.payee}: goods or services received; expect a disclosure statement"
            )


@rule(
    "R-DON-08",
    "Acknowledgment must be for this year and contemporaneous",
    "error",
    "possible_inconsistency",
)
def ack_checks(ctx: Ctx) -> Iterator[Flag]:
    """Metadata checks only; donor, amount and goods-or-services wording need extraction."""
    deadline = ctx.filing_deadline()
    for c in ctx.claims_of("donation"):
        for link in c.evidence:
            doc = ctx.doc(link.doc)
            if (
                doc is None
                or doc.classification is None
                or doc.classification.doc_type != "donation_ack"
            ):
                continue
            year = doc.classification.tax_year
            if year is not None and year != c.year:
                yield Flag(
                    [c.id, doc.id], f"{c.payee}: acknowledgment {doc.id} is for {year}", key=doc.id
                )
            if doc.acquired.at.date() > deadline:
                yield Flag(
                    [c.id, doc.id],
                    f"{c.payee}: acknowledgment {doc.id} obtained after the filing deadline "
                    "(not contemporaneous)",
                    key=f"{doc.id}.late",
                )


@rule("R-DON-09", "One acknowledgment linked to several gifts", "info", "change")
def aggregate_ack(ctx: Ctx) -> Iterator[Flag]:
    uses: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in ctx.claims_of("donation"):
        for link in c.evidence:
            uses[(link.doc, c.payee.lower())].append(c.id)
    for (doc, _payee), claim_ids in uses.items():
        if len(claim_ids) > 1:
            yield Flag(
                [doc, *claim_ids],
                f"{doc} is linked to {len(claim_ids)} gifts; it covers them only if it itemizes "
                "each gift or its total equals their sum",
            )


@rule(
    "R-DON-10",
    "Donation assertions disagree with no resolution",
    "warning",
    "possible_inconsistency",
)
def assertions_disagree(ctx: Ctx) -> Iterator[Flag]:
    tolerance = Decimal(str(ctx.param("R-DON-10", "tolerance")))
    for c in ctx.claims_of("donation"):
        if c.resolved is None and _disagreeing(c, tolerance):
            listed = "; ".join(f"{_money(a.value)} ({a.source})" for a in c.assertions)
            yield Flag(
                [c.id],
                f"{c.payee}: amounts disagree: {listed}",
                suggested_action="Resolve by citing the acknowledgment or a note",
            )
