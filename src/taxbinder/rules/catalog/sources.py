"""Sources and institutions: R-SRC-01…06."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from itertools import combinations

from taxbinder.rules.engine import Ctx, Flag, rule


@rule(
    "R-SRC-01",
    "Doc type produced last year has no expectation this year",
    "error",
    "missing_evidence",
)
def prior_year_gap(ctx: Ctx) -> Iterator[Flag]:
    current = {(e.source, e.doc_type) for e in ctx.expectations}
    for e in ctx.prior_expectations:
        if e.applicability == "applicable" and (e.source, e.doc_type) not in current:
            yield Flag(
                [e.source],
                f"{e.source} produced {e.doc_type} for {ctx.year - 1} but has no {ctx.year} "
                "expectation or applicability decision",
                key=e.doc_type,
                suggested_action="Generate the expectation or record why it doesn't apply",
            )


def _overlap(
    a_from: date | None, a_to: date | None, b_from: date | None, b_to: date | None
) -> bool:
    return (a_from or date.min) <= (b_to or date.max) and (b_from or date.min) <= (a_to or date.max)


@rule(
    "R-SRC-02", "Two sources claim the same account identifier", "warning", "possible_inconsistency"
)
def duplicate_identifier(ctx: Ctx) -> Iterator[Flag]:
    ids = [(s.id, i) for s in ctx.sources for i in s.identifiers]
    for (sa, a), (sb, b) in combinations(ids, 2):
        if sa == sb or not a.matches(b.number) or not b.matches(a.number):
            continue
        if _overlap(a.effective_from, a.effective_to, b.effective_from, b.effective_to):
            first, second = sorted([sa, sb])
            yield Flag(
                [first, second],
                f"{first} and {second} both list account {a.hint or '…'} for overlapping periods",
                key=second,
                suggested_action="Correct the identifier on one source, or merge the sources",
            )


# Papers that back a claim or are the return itself: no checklist row is ever for them.
NOT_ROWS = {
    "tax_return", "return_paper", "donation_ack", "form_8283_support", "payment_confirmation",
    "property_tax_bill", "vehicle_registration", "account_change_notice",
}  # fmt: skip


@rule(
    "R-SRC-03",
    "Document from an issuer with no matching source",
    "warning",
    "possible_inconsistency",
)
def unknown_issuer(ctx: Ctx) -> Iterator[Flag]:
    """A form that a checklist row could be for, from an issuer with none: it isn't on the
    checklist yet. A year with no rows at all (a new household) gets one finding, not one per
    document."""
    from taxbinder.report import DOC_LABELS

    with_sources = {s.institution for s in ctx.sources}
    names = {i.id: i.name for i in ctx.institutions}
    off = []
    for d in ctx.documents:
        c = d.classification
        if c is None or c.tax_year != ctx.year or c.source is not None:
            continue
        if c.doc_type is None or c.doc_type in NOT_ROWS:
            continue
        if c.issuer is None or c.issuer not in with_sources:
            off.append(d)
    if off and not ctx.expectations:
        yield Flag(
            [d.id for d in off],
            f"{len(off)} documents for {ctx.year} aren't on a checklist yet",
            key="no-checklist",
            suggested_action="Add them to the checklist",
        )
        return
    for d in off:
        c = d.classification
        what = DOC_LABELS.get(c.doc_type, c.doc_type)
        sender = f" from {names.get(c.issuer, c.issuer)}" if c.issuer else ""
        yield Flag(
            [d.id],
            f"{d.original_filename} ({what}{sender}) isn't on the checklist yet",
            suggested_action="Add it to the checklist or link it to a row",
        )


@rule("R-SRC-04", "Known institution, no matching source", "warning", "possible_inconsistency")
def unknown_account(ctx: Ctx) -> Iterator[Flag]:
    """A known institution's document that matched no source: an unknown account number
    (propose adding it to a source) or several possible sources (pick one)."""
    for d in ctx.documents:
        c = d.classification
        if c is None or c.tax_year != ctx.year or c.source or not c.issuer:
            continue
        candidates = [s for s in ctx.sources if s.institution == c.issuer]
        if not candidates:
            continue  # R-SRC-03
        options = ", ".join(s.id for s in candidates)
        if c.account_number and not any(
            i.matches(c.account_number) for s in ctx.sources for i in s.identifiers
        ):
            shown = "…" + c.account_number[-4:]
            yield Flag(
                [d.id],
                f"{d.original_filename}: {c.issuer} account {shown} matches no source identifier",
                suggested_action=f"Add it as an identifier of one of: {options}",
            )
        else:
            yield Flag(
                [d.id],
                f"{d.original_filename}: {c.issuer} {c.doc_type or 'document'} could not be "
                "matched to one source",
                suggested_action=f"Link it to one of: {options}",
            )


@rule(
    "R-SRC-05",
    "Identifier changed during the year: expect both periods",
    "error",
    "missing_evidence",
)
def split_reporting(ctx: Ctx) -> Iterator[Flag]:
    start, end = date(ctx.year, 1, 1), date(ctx.year, 12, 31)
    for s in ctx.sources:
        changed = any(
            (i.effective_from and start < i.effective_from <= end)
            or (i.effective_to and start <= i.effective_to < end)
            for i in s.identifiers
        )
        if not changed:
            continue
        for e in ctx.expectations:
            if e.source == s.id and e.applicability == "applicable" and len(e.evidence) < 2:
                yield Flag(
                    [e.id, s.id],
                    f"{s.id} changed account numbers in {ctx.year}; {e.doc_type} needs documents "
                    f"covering both periods ({len(e.evidence)} linked)",
                )


@rule("R-SRC-06", "Institution acquired or renamed: review its portal", "info", "change")
def lineage_change(ctx: Ctx) -> Iterator[Flag]:
    since = ctx.today - timedelta(days=ctx.param("R-SRC-06", "lookback_days"))
    for inst in ctx.institutions:
        for link in inst.lineage:
            if link.effective and link.effective >= since:
                yield Flag(
                    [inst.id],
                    f"{inst.name}: {link.relation} {link.institution} on {link.effective}; "
                    "check the portal for its sources",
                    key=f"{link.relation}.{link.institution}",
                )
