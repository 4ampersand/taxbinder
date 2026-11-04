"""Donations, shared by the CLI and the app: add, list with the evidence each one needs (§8.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from taxbinder.claims import free_claim_id
from taxbinder.models import CLAIM_ADAPTER
from taxbinder.store import RecordStore

DONATION_KINDS = {
    "cash": "Cash, check or card",
    "securities": "Stock or other securities",
    "property": "Property (goods, art, a car …)",
    "daf_grant": "Grant from my donor-advised fund",
}
LETTER_REQS = ("written_acknowledgment", "written_acknowledgment_daf")
# Records a stock or property gift needs (the preparer turns them into the right tax form).
RECORD_REQS = ("form_8283_section_a", "brokerage_transfer_confirmation")
APPRAISAL_REQS = ("qualified_appraisal",)


def add_donation(
    store: RecordStore,
    payee: str,
    amount: Decimal,
    day: date,
    kind: str,
    actor: str,
    to_daf: bool = False,
    security: str = "",
    shares: Decimal | None = None,
    cost_basis_per_share: Decimal | None = None,
    acquired: date | None = None,
    quid_pro_quo: bool = False,
) -> str:
    """Record a gift; the checks then say which letters and forms it needs. Returns its id."""
    payee = payee.strip()
    if not payee:
        raise ValueError("say which charity it went to")
    if kind not in DONATION_KINDS:
        raise ValueError(f"unknown kind of gift {kind!r}")
    if amount is None or amount <= 0:
        raise ValueError("the amount must be more than zero")
    details: dict[str, Any] = {"type": kind, "to_daf": to_daf or None}
    if quid_pro_quo:
        details["quid_pro_quo"] = True
    if kind == "securities":
        details |= {
            "security": security.strip() or None,
            "shares": shares,
            "cost_basis_per_share": cost_basis_per_share,
            "acquired": acquired,
        }
        if acquired is not None:  # held more than a year → long-term
            long_term = day - acquired > timedelta(days=365)
            details["holding_period"] = "long_term" if long_term else "short_term"
    slug = re.sub(r"[^a-z0-9]+", "-", payee.lower()).strip("-")[:40].strip("-") or "gift"
    claim = CLAIM_ADAPTER.validate_python(
        {
            "id": free_claim_id(store, f"{day.year}/donations/{day}.{slug}"),
            "year": day.year,
            "category": "donation",
            "date": day,
            "amount": amount,
            "payee": payee,
            "entered_by": actor,
            "entered_at": datetime.now(UTC),
            "provenance": "hand_entry",
            "donation": details,
        }
    )
    with store.operation(actor, f"donation {payee} {amount}"):
        store.put(claim, None)
    return claim.id


def _state(c: Any, ids: tuple[str, ...]) -> tuple[str, str, str]:  # noqa: ANN401
    reqs = [r for r in c.requirements if r.id in ids]
    if not reqs:
        return "none", "Not needed", "dim"
    if all(r.satisfied_by for r in reqs):
        return "have", "In the vault", "ok"
    if all(r.satisfied_by or r.waived for r in reqs):
        return "waived", "Waived", "dim"
    return "missing", "Needed", "warn"


@dataclass
class DonationRow:
    c: Any
    kind: str
    letter: tuple[str, str, str]
    records: tuple[str, str, str]
    appraisal: tuple[str, str, str]
    files: list[str]

    @property
    def needs_paperwork(self) -> bool:
        return "missing" in (self.letter[0], self.records[0], self.appraisal[0])

    @property
    def record_name(self) -> str:
        return "transfer record" if self.c.donation.type == "securities" else "gift records"

    @property
    def paperwork(self) -> tuple[str, str]:
        """One plain status for the row: (label, pill class)."""
        missing = [
            what
            for what, state in (
                ("letter", self.letter),
                (self.record_name, self.records),
                ("appraisal", self.appraisal),
            )
            if state[0] == "missing"
        ]
        if missing:
            return " and ".join(missing).capitalize() + " needed", "warn"
        if any(s[0] in ("have", "waived") for s in (self.letter, self.records, self.appraisal)):
            return "Complete", "ok"
        return "Not needed", "dim"

    @property
    def noncash(self) -> bool:
        return self.c.donation.type in ("securities", "property")


def donation_rows(store: RecordStore, year: int) -> list[DonationRow]:
    rows = [
        DonationRow(
            c=c,
            kind=DONATION_KINDS.get(c.donation.type, c.donation.type)
            + (" · to a DAF" if c.donation.to_daf else ""),
            letter=_state(c, LETTER_REQS),
            records=_state(c, RECORD_REQS),
            appraisal=_state(c, APPRAISAL_REQS),
            files=[link.doc for link in c.evidence if link.role != "superseded"],
        )
        for c in store.list("claim", year)
        if c.category == "donation"
    ]
    rows.sort(key=lambda r: (r.c.date, r.c.payee.lower()))
    return rows


def summary(rows: list[DonationRow]) -> dict[str, Any]:
    cash = sum((r.c.amount for r in rows if not r.noncash), Decimal(0))
    noncash = sum((r.c.amount for r in rows if r.noncash), Decimal(0))
    return {
        "total": cash + noncash,
        "cash": cash,
        "noncash": noncash,
        "letters_missing": sum(r.letter[0] == "missing" for r in rows),
        "forms_missing": sum("missing" in (r.records[0], r.appraisal[0]) for r in rows),
    }


def edit_donation(store: RecordStore, claim_id: str, actor: str, **fields: Any) -> str:  # noqa: ANN401
    """Change a gift (payee, amount, date, kind, DAF, something in return, security details);
    keeps its files. A date in another year moves it there. Returns its (possibly new) id."""
    claim, rev = store.get("claim", claim_id)
    if claim.category != "donation":
        raise ValueError("only donations can be edited here")
    details = claim.donation.model_dump()
    top: dict[str, Any] = {}
    if "payee" in fields:
        if not str(fields["payee"]).strip():
            raise ValueError("say which charity it went to")
        top["payee"] = str(fields["payee"]).strip()
    if "amount" in fields:
        if fields["amount"] is None or fields["amount"] <= 0:
            raise ValueError("the amount must be more than zero")
        top["amount"] = fields["amount"]
    if "date" in fields:
        top["date"] = fields["date"]
    if "kind" in fields:
        if fields["kind"] not in DONATION_KINDS:
            raise ValueError(f"unknown kind of gift {fields['kind']!r}")
        details["type"] = fields["kind"]
    for key, name in (("to_daf", "to_daf"), ("quid_pro_quo", "quid_pro_quo")):
        if key in fields:
            details[name] = bool(fields[key]) or None
    for key in ("security", "shares", "cost_basis_per_share", "acquired"):
        if key in fields:
            details[key] = fields[key] or None
    day = top.get("date", claim.date)
    if details.get("acquired"):
        details["holding_period"] = (
            "long_term" if day - details["acquired"] > timedelta(days=365) else "short_term"
        )
    data = {**claim.model_dump(), **top, "donation": details}
    moved = day.year != claim.year
    if moved:
        rest = claim.id.split("/", 1)[1]
        data |= {"id": free_claim_id(store, f"{day.year}/{rest}"), "year": day.year}
    updated = CLAIM_ADAPTER.validate_python(data)
    if updated == claim:
        return claim_id
    with store.operation(actor, f"donation edit {claim_id}"):
        if moved:
            store.put(updated, None)
            store.delete("claim", claim_id, rev)
        else:
            store.put(updated, rev)
    return updated.id


@dataclass
class GiftLine:
    """One charity across the years (Across the years): what was given to it each year."""

    name: str
    from_daf: bool  # grants out of a donor-advised fund: not given again, so not in the total
    years: dict[int, Decimal]


def by_year(store: RecordStore) -> tuple[list[GiftLine], dict[int, Decimal]]:
    """Every charity with its gifts summed per year (the name as typed, case and spacing
    ignored), and the total given each year. A DAF grant was counted when the money went into
    the fund, so it has its own line and stays out of the total."""
    lines: dict[tuple[str, bool], GiftLine] = {}
    total: dict[int, Decimal] = {}
    for c in store.list("claim"):
        if c.category != "donation":
            continue
        daf = c.donation.type == "daf_grant"
        key = (" ".join(c.payee.split()).casefold(), daf)
        line = lines.setdefault(key, GiftLine(" ".join(c.payee.split()), daf, {}))
        line.years[c.year] = line.years.get(c.year, Decimal(0)) + c.amount
        if not daf:
            total[c.year] = total.get(c.year, Decimal(0)) + c.amount
    return sorted(lines.values(), key=lambda g: (g.from_daf, g.name.casefold())), total
