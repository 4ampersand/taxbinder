"""Returns: R-RET-01."""

from __future__ import annotations

from collections.abc import Iterator

from taxbinder.rules.engine import Ctx, Flag, rule


@rule("R-RET-01", "Federal return not filed near its deadline", "warning", "missing_evidence")
def not_filed(ctx: Ctx) -> Iterator[Flag]:
    """Reminded on R-EXP-01's days before the filing deadline (the extended one after a 4868);
    filed means marked filed or accepted on the year's cover, or a filed copy here."""
    deadline = ctx.filing_deadline()
    days_left = (deadline - ctx.today).days
    if days_left > max(ctx.param("R-EXP-01", "days")):
        return
    said = next((t for t in ctx.year_config.returns if t.jurisdiction == "federal"), None)
    if said and said.status in ("filed", "accepted", "not_needed"):
        return
    if any(
        d.tax_return.jurisdiction == "federal"
        and d.tax_return.status == "filed"
        and d.classification.doc_type == "tax_return"
        and d.classification.tax_year == ctx.year
        for d in ctx.documents
        if d.tax_return and d.classification
    ):
        return
    when = (
        f"{days_left} days to the {deadline} deadline"
        if days_left >= 0
        else f"the {deadline} deadline passed"
    )
    yield Flag(
        [str(ctx.year)],
        f"the {ctx.year} federal return isn't marked filed; {when}",
        key="federal",
        suggested_action="Once it's filed, say so under Returns on the year's cover",
    )
