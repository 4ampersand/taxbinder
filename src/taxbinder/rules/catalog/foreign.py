"""Foreign accounts: R-FOR-01."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal

from taxbinder.rules.engine import Ctx, Flag, rule


@rule(
    "R-FOR-01",
    "Foreign accounts over the FBAR threshold need a FinCEN 114 filing",
    "error",
    "missing_evidence",
)
def fbar_required(ctx: Ctx) -> Iterator[Flag]:
    threshold = Decimal(str(ctx.param("R-FOR-01", "fbar_aggregate_threshold")))
    default_rate = ctx.param("R-FOR-01", "treasury_rate_default")
    totals: dict[str, Decimal] = defaultdict(Decimal)
    claims: dict[str, list[str]] = defaultdict(list)
    joint: list[tuple[Decimal, str]] = []
    for c in ctx.claims_of("fbar"):
        fa = c.foreign_account
        rate = fa.rate or (Decimal(str(default_rate)) if default_rate else None)
        usd = fa.usd_value or (
            fa.max_balance if fa.currency == "USD" else fa.max_balance / rate if rate else None
        )
        if usd is None:
            yield Flag([c.id], f"{c.id}: no exchange rate to convert {fa.currency}", key="rate")
            continue
        if c.taxpayer is None:  # joint: counted in full for each owner (below)
            joint.append((usd, c.id))
            continue
        totals[c.taxpayer] += usd
        claims[c.taxpayer].append(c.id)
    for owner in list(totals) or ["unknown"]:
        for usd, claim_id in joint:
            totals[owner] += usd
            claims[owner].append(claim_id)

    filed = any(
        d.classification
        and d.classification.doc_type == "fbar_filing"
        and d.classification.tax_year == ctx.year
        for d in ctx.documents
    ) or any(
        e.doc_type == "fbar_filing" and e.acquisition in ("received", "vaulted")
        for e in ctx.expectations
    )
    if filed:
        return
    for owner, total in totals.items():
        if total > threshold:
            yield Flag(
                [owner, *claims[owner]],
                f"{owner}: foreign accounts peaked at about ${total:,.0f} in aggregate "
                f"(> ${threshold:,.0f}); FinCEN 114 (FBAR) required and no filing evidence linked",
                suggested_action="File FBAR and vault the BSA E-Filing confirmation",
            )
