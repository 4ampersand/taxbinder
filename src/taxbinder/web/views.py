"""What the pages show, computed from records: status pills, row groups, progress and tiles.

Kept out of the templates so the wording and the rules for it are in one tested place.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from taxbinder import perf
from taxbinder.report import DOC_LABELS
from taxbinder.store import RecordNotFound, RecordStore

# Groups for "Group by: Kind", in display order.
KINDS: list[tuple[str, set[str]]] = [
    ("Partnerships and K-1s", {"k1_1065", "k1_state", "k1_1120s", "k1_1041"}),
    (
        "Brokerage and banking",
        {"1099_composite", "1099_int", "1099_div", "1099_misc", "1099_nec", "1099_q"},
    ),
    ("Retirement and HSA", {"1099_r", "5498", "5498_sa"}),
    ("Wages and benefits", {"w2", "ssa_1099"}),
    ("Home and property", {"1098", "property_tax_bill", "vehicle_registration"}),
    ("Payments", {"payment_confirmation"}),
    ("Gifts", {"donation_ack", "form_8283_support"}),
    ("Foreign accounts", {"foreign_bank_statement", "fbar_filing"}),
]
OTHER_KIND = "Other"
NOT_APPLICABLE = {
    "deferred_next_year": "Next year",
    "not_issued": "Not issued",
    "below_threshold": "Below threshold",
    "final_year_prior": "Ended last year",
    "consolidated_elsewhere": "In another form",
}


def kind_of(doc_type: str) -> str:
    return next((name for name, types in KINDS if doc_type in types), OTHER_KIND)


def kind_rank(kind: str) -> int:
    names = [name for name, _ in KINDS]
    return names.index(kind) if kind in names else len(names)


def status(e: Any, usually: str = "") -> tuple[str, str, str]:  # noqa: ANN401
    """(key, label, pill class): one pill for the row's whole lifecycle. `usually`: when last
    year's arrived ("often March")."""
    if e.applicability != "applicable":
        return "na", NOT_APPLICABLE.get(e.applicability, "Not applicable"), "dim"
    if e.acquisition == "superseded":
        return "superseded", "Replaced", "dim"
    if e.acquisition == "vaulted":
        return (
            ("checked", "✓ Checked", "ok")
            if e.review == "verified"
            else ("vaulted", "In the vault", "info")
        )
    if e.acquisition == "received":
        return "received", "Received", "warn"  # received but not yet in the vault
    return "to_get", f"To come · {usually}" if usually else "To come", "warn"


@dataclass
class Row:
    e: Any
    institution: str
    source: str
    owner: str
    doc_label: str
    kind: str
    status: tuple[str, str, str]
    files: list[str] = field(default_factory=list)
    portal: str = ""
    usually: str = ""  # "often March": when last year's came

    @property
    def portal_host(self) -> str:
        from urllib.parse import urlsplit

        return (urlsplit(self.portal).hostname or "").removeprefix("www.")


@perf.timed("checklist_rows")
def checklist_rows(store: RecordStore, year: int) -> list[Row]:
    sources = {s.id: s for s in store.list("source")}
    institutions = {i.id: i for i in store.list("institution")}
    from taxbinder.entities import entity_info
    from taxbinder.years import EVERYONE

    owners = {EVERYONE: entity_info(store).name, **{t.id: t.name for t in store.list("taxpayer")}}
    labels = {**DOC_LABELS, **{d.id: d.name for d in store.list("doctype")}}
    came = arrivals(store, year - 1)
    rows = []
    for e in store.list("expectation", year):
        usually = came.get((e.source, e.doc_type), "")
        src = sources.get(e.source)
        inst = institutions.get(src.institution) if src else None
        rows.append(
            Row(
                e=e,
                institution=inst.name if inst else "",
                source=src.name if src else e.source,
                owner=owners.get(src.owner, src.owner) if src else "",
                doc_label=labels.get(e.doc_type, e.doc_type),
                kind=kind_of(e.doc_type),
                status=status(e, usually),
                files=[link.doc for link in e.evidence if link.role != "superseded"],
                portal=(inst.portal.url or "") if inst and inst.portal else "",
                usually=usually,
            )
        )
    rows.sort(key=lambda r: (kind_rank(r.kind), r.institution.lower(), r.source.lower()))
    return rows


def arrivals(store: RecordStore, year: int) -> dict[tuple[str, str], str]:
    """(source, document type) → "often March": the month the year's document came."""
    docs = {d.id: d for d in store.list("document")}
    out = {}
    for e in store.list("expectation", year):
        link = next((x for x in e.evidence if x.role == "primary" and x.doc in docs), None)
        if link is not None:
            out[(e.source, e.doc_type)] = f"often {docs[link.doc].acquired.at:%B}"
    return out


def open_count(store: RecordStore, year: int) -> int:
    return sum(
        1
        for e in store.list("expectation", year)
        if e.applicability == "applicable"
        and e.acquisition in ("expected", "available", "received")
    )


def progress(store: RecordStore, year: int) -> dict[str, Any]:
    applicable = [e for e in store.list("expectation", year) if e.applicability == "applicable"]
    verified = sum(e.acquisition == "vaulted" and e.review == "verified" for e in applicable)
    vaulted = sum(e.acquisition == "vaulted" and e.review != "verified" for e in applicable)
    to_get = len(applicable) - verified - vaulted
    total = len(applicable) or 1
    return {
        "verified": verified,
        "vaulted": vaulted,
        "to_get": to_get,
        "have": verified + vaulted,
        "p_verified": round(100 * verified / total, 1),
        "p_vaulted": round(100 * vaulted / total, 1),
    }


def tiles(store: RecordStore, year: int) -> dict[str, Any]:
    payments: dict[str, Decimal] = defaultdict(Decimal)
    gifts, gifts_total = 0, Decimal(0)
    for c in store.list("claim", year):
        if c.unpaid:  # planned or scheduled: not paid yet
            continue
        if c.category == "tax_payment":
            payments["Federal" if c.jurisdiction == "federal" else c.jurisdiction] += c.amount
        elif c.category == "donation":
            gifts, gifts_total = gifts + 1, gifts_total + c.amount
    ordered = sorted(payments.items(), key=lambda kv: (kv[0] != "Federal", kv[0]))
    return {
        "payments": ordered,
        "payments_total": sum(payments.values(), Decimal(0)),
        "gifts": gifts,
        "gifts_total": gifts_total,
    }


def extension_filed(store: RecordStore, year: int) -> bool:
    from taxbinder.models import YearConfig
    from taxbinder.returns import extended

    try:
        config = store.get("year", str(year))[0]
    except RecordNotFound:
        config = YearConfig(year=year)
    return extended(config, store.list("document"))


def carry_over_rows(store: RecordStore, rows: list, labels: dict[str, str]) -> list[dict]:
    """Last year's rows for the Start-next-year dialog, in checklist order."""
    sources = {s.id: s for s in store.list("source")}
    institutions = {i.id: i.name for i in store.list("institution")}
    out = []
    for c in rows:
        src = sources.get(c.row.source)
        out.append(
            {
                "id": c.row.id,
                "institution": institutions.get(src.institution, "") if src else "",
                "source": src.name if src else c.row.source,
                "form": labels.get(c.row.doc_type, c.row.doc_type),
                "kind": kind_of(c.row.doc_type),
                "keep": c.keep,
                "why": c.why,
            }
        )
    out.sort(key=lambda r: (kind_rank(r["kind"]), r["institution"].lower(), r["source"].lower()))
    return out
