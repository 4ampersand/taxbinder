"""Year status, organised around three questions: what is missing, what conflicts, and what
needs my attention (§10). Used by `taxbinder status` and the app."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from taxbinder.models import Finding, YearConfig
from taxbinder.rules.params import load_params
from taxbinder.store import RecordNotFound, RecordStore

GROUPS = {
    "missing_evidence": "missing",
    "possible_inconsistency": "conflicts",
    "demonstrated_error": "conflicts",
    "change": "attention",
}
# §11.2 "What needs my attention?": review queue, follow-ups, stale deliveries.
ATTENTION_RULES = {
    "R-EXP-01",
    "R-EXP-04",
    "R-EXP-05",
    "R-EXP-06",
    "R-EXP-07",
    "R-BIL-01",
    "R-BIL-02",
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def group_of(f: Finding) -> str:
    return "attention" if f.rule in ATTENTION_RULES else GROUPS[f.classification]


# Human labels for document types (app and CPA package).
DOC_LABELS = {
    "w2": "W-2",
    "1099_composite": "1099",
    "1099_int": "1099-INT",
    "1099_div": "1099-DIV",
    "1099_q": "1099-Q",
    "1099_r": "1099-R",
    "1099_misc": "1099-MISC",
    "1099_nec": "1099-NEC",
    "ssa_1099": "SSA-1099",
    "k1_1065": "K1",
    "k1_state": "State K1",
    "k1_1120s": "K1 (1120-S)",
    "k1_1041": "K1 (1041)",
    "1098": "1098",
    "5498": "5498",
    "5498_sa": "5498-SA",
    "payment_confirmation": "Payment confirmation",
    "donation_ack": "Donation receipt",
    "form_8283_support": "Non-cash gift records (Form 8283)",
    "property_tax_bill": "Property tax",
    "vehicle_registration": "Vehicle registration",
    "foreign_bank_statement": "Bank statement",
    "fbar_filing": "FBAR filing",
    "account_change_notice": "Account change notice",
    "tax_return": "Tax return",
    "return_paper": "Return paper",
}


@dataclass
class YearStatus:
    year: int
    today: date
    deadline: date | None
    expectations: int = 0
    applicable: int = 0
    acquisition: Counter[str] = field(default_factory=Counter)
    verified: int = 0
    outstanding: list[str] = field(default_factory=list)  # applicable, not in the vault yet
    claims: int = 0
    unreconciled: int = 0
    unreconciled_amount: Decimal = Decimal(0)
    reconciled_at: str | None = None
    params_reviewed: bool = False
    checked: bool = True  # False: no rule parameters for the year, so nothing is checked
    checks_start: int | None = None  # the first year that is checked
    findings: dict[str, list[Finding]] = field(
        default_factory=lambda: {"missing": [], "conflicts": [], "attention": []}
    )

    @property
    def days_left(self) -> int | None:
        return (self.deadline - self.today).days if self.deadline else None

    def counts(self) -> Counter[str]:
        return Counter(f.severity for group in self.findings.values() for f in group)


def _deadline(year_config: YearConfig, year: int, form: str = "1040") -> date | None:
    if "filing" in year_config.deadlines:
        return year_config.deadlines["filing"]
    try:
        params = load_params(year, form)
    except ValueError:  # no params file for this year
        return None
    key = "extended_deadline" if year_config.extension_filed else "deadline"
    return params.values.get("R-EXP-01", {}).get(key)


def year_status(store: RecordStore, year: int, today: date) -> YearStatus:
    from taxbinder.entities import entity_info  # the return form picks the rule parameters

    form = entity_info(store).form
    try:
        year_config, _ = store.get("year", str(year))
    except RecordNotFound:
        year_config = YearConfig(year=year)
    from taxbinder.rules.params import checked_years

    checked = True
    try:
        reviewed = load_params(year, form).reviewed
    except ValueError:  # no params file for this year: it isn't checked
        reviewed, checked = False, False
    status = YearStatus(year, today, _deadline(year_config, year, form), params_reviewed=reviewed)
    status.checked = checked
    status.checks_start = next(iter(checked_years(form)), None)

    for e in store.list("expectation", year):
        status.expectations += 1
        if e.applicability != "applicable":
            continue
        status.applicable += 1
        status.acquisition[e.acquisition] += 1
        status.verified += e.review == "verified"
        if e.acquisition not in ("vaulted", "superseded"):
            status.outstanding.append(f"{e.id} ({e.acquisition})")

    for c in store.list("claim", year):
        status.claims += 1
        if c.status == "evidence_requested":
            status.unreconciled += 1
            status.unreconciled_amount += c.amount or 0

    try:
        findings, _ = store.get("findings", str(year))
    except RecordNotFound:
        return status
    status.reconciled_at = findings.ran_at.isoformat(timespec="minutes")
    for f in sorted(findings.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.id)):
        if f.status == "open":
            status.findings[group_of(f)].append(f)
    return status


def format_status(s: YearStatus, outstanding: bool = False, limit: int | None = 10) -> str:
    lines = [f"TaxBinder {s.year}"]
    if s.deadline:
        lines.append(f"Deadline {s.deadline} ({s.days_left} days)")
    acq = ", ".join(f"{k} {v}" for k, v in sorted(s.acquisition.items()))
    lines.append(
        f"Expectations: {s.expectations} ({s.applicable} applicable: {acq}; verified {s.verified})"
    )
    lines.append(
        f"Claims: {s.claims} ({s.unreconciled} unreconciled, ${s.unreconciled_amount:,.2f})"
    )
    if not s.checked:
        start = f": checks start with {s.checks_start}" if s.checks_start else ""
        lines.append(f"{s.year} isn't checked{start}")
        return "\n".join(lines)
    if not s.params_reviewed:
        lines.append(f"Rule parameters for {s.year} not yet reviewed")
    if s.reconciled_at is None:
        lines.append("Not reconciled yet")
    else:
        c = s.counts()
        lines.append(
            f"Open findings: {c['error']} errors, {c['warning']} warnings, {c['info']} info "
            f"(reconciled {s.reconciled_at})"
        )
        titles = {
            "missing": "What is missing?",
            "conflicts": "What conflicts?",
            "attention": "What needs my attention?",
        }
        for group, title in titles.items():
            items = s.findings[group]
            lines.append(f"\n{title} ({len(items)})")
            for f in items[:limit] if limit else items:
                lines.append(f"  [{f.severity}] {f.message}")
                lines.append(f"      {f.id}")
            if limit and len(items) > limit:
                lines.append(f"  … {len(items) - limit} more (--all)")
    if outstanding:
        lines.append(f"\nOutstanding ({len(s.outstanding)})")
        lines.extend(f"  {item}" for item in s.outstanding)
    return "\n".join(lines)
