"""Years and checklist rows, shared by the CLI and the app: start a year, add or delete a row."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from taxbinder.doctypes import is_known
from taxbinder.models import Expectation, Institution, Source, YearConfig
from taxbinder.models.common import DocType
from taxbinder.models.expectation import Event
from taxbinder.models.registry import Produces
from taxbinder.slugs import EXPECTATION_SUFFIX, institution_slug, slugify
from taxbinder.store import RecordNotFound, RecordStore


def years_in_vault(store: RecordStore) -> list[int]:
    return sorted({e.year for e in store.list("expectation")})


def expectation_id(year: int, source_id: str, doc_type: str) -> str:
    """Row ids look like `2025/fernhollow-properties.kettle-410.k1`."""
    return f"{year}/{source_id}.{EXPECTATION_SUFFIX.get(doc_type, doc_type.replace('_', '-'))}"


@dataclass
class CarryOver:
    """One of last year's rows when starting a year: kept, or suggested to drop and why."""

    row: Expectation
    keep: bool
    why: str = ""


def carry_over(store: RecordStore, year: int, from_year: int) -> list[CarryOver]:
    """`from_year`'s rows not yet in `year`; suggested drops: those that won't recur."""
    have = {(e.source, e.doc_type) for e in store.list("expectation", year)}
    sources = {s.id: s for s in store.list("source")}
    out = []
    for e in store.list("expectation", from_year):
        if (e.source, e.doc_type) in have:
            continue
        src = sources.get(e.source)
        if src and src.closed and src.closed.year < year:
            why = f"closed {src.closed.isoformat()}"
        elif src and src.last_tax_year is not None and src.last_tax_year < year:
            why = f"last one was for {src.last_tax_year}"
        elif e.applicability == "final_year_prior":
            why = "ended last year"
        else:
            why = ""
        out.append(CarryOver(e, keep=not why, why=why))
    return out


def new_year(
    store: RecordStore, year: int, from_year: int, actor: str, drop: set[str] | None = None
) -> int:
    """Start `year` from `from_year`'s rows: same sources and document types, statuses reset.

    Only what `year` doesn't have yet is added, so starting it twice creates nothing twice.
    `drop` (row ids of `from_year`) leaves rows out and marks their source as ended with
    `from_year`; without it, the suggested drops are left out. Returns the number of rows added.
    Going back (an earlier year from the first one) every row is kept unless dropped, and a
    dropped row ends nothing.
    """
    if not store.list("expectation", from_year):
        raise ValueError(f"{from_year} has no checklist rows to copy")
    rows = carry_over(store, year, from_year)
    if drop is None:
        drop = {c.row.id for c in rows if not c.keep} if year > from_year else set()
    if year < from_year and rows and all(c.row.id in drop for c in rows):
        raise ValueError("keep at least one row: a year shows once it has one")
    settings: dict = {}
    try:
        previous, _ = store.get("year", str(from_year))
        settings = {
            "filing_status": previous.filing_status,
            # after a move: the year after in the state moved to, the year before in the first
            "resident_states": previous.resident_states[-1:] if year > from_year
            else previous.resident_states[:1],
            "preparer": previous.preparer,
        }
    except RecordNotFound:
        pass
    added = 0
    with store.operation(actor, f"year new {year} (from {from_year})"):
        try:
            store.get("year", str(year))
        except RecordNotFound:
            store.put(YearConfig(year=year, **settings), None)
        kept = {c.row.source for c in rows if c.row.id not in drop}
        kept |= {e.source for e in store.list("expectation", year)}
        ended = {c.row.source for c in rows} - kept if year > from_year else set()
        for source in ended:  # every row of it dropped: it ended
            src, rev = store.get("source", source)
            if src.last_tax_year is None or src.last_tax_year >= year:
                store.put(src.model_copy(update={"last_tax_year": from_year}), rev)
        for c in rows:
            e = c.row
            if e.id in drop:
                continue
            new = Expectation(
                id=expectation_id(year, e.source, e.doc_type),
                year=year,
                source=e.source,
                doc_type=e.doc_type,
                split_reporting=e.split_reporting,
                priority=e.priority,
            )
            store.put(new, None)
            added += 1
    return added


EVERYONE = "household"  # a row's owner before (or instead of) one person: the whole filer


def default_owner(store: RecordStore) -> str:
    """The taxpayer, once the household is known; until then the household as a whole."""
    ids = {t.id for t in store.list("taxpayer")}
    return "primary" if "primary" in ids else EVERYONE


def add_row(
    store: RecordStore,
    year: int,
    institution: str,
    source: str,
    doc_type: DocType,
    owner: str,
    actor: str,
    amount: Decimal | None = None,
    comment: str = "",
) -> str:
    """Add one checklist row; the institution and source are created when new. Returns its id."""
    institution, source = institution.strip(), source.strip()
    if not institution or not source:
        raise ValueError("institution and source are both required")
    owner = owner or default_owner(store)
    if owner != EVERYONE:
        store.get("taxpayer", owner)  # RecordNotFound for an unknown owner
    if not is_known(store, doc_type):
        raise ValueError(f"unknown document type {doc_type!r}")
    inst_id = institution_slug(institution)
    if not inst_id or not slugify(source):
        raise ValueError("institution and source need letters or digits")
    source_id = f"{inst_id}.{slugify(source)}"
    exp_id = expectation_id(year, source_id, doc_type)
    try:
        store.get("expectation", exp_id)
        raise ValueError(f"{exp_id} already exists")
    except RecordNotFound:
        pass
    with store.operation(actor, f"row add {exp_id}"):
        try:
            store.get("institution", inst_id)
        except RecordNotFound:
            store.put(Institution(id=inst_id, name=institution, kind="other"), None)
        try:
            src, rev = store.get("source", source_id)
            update: dict = {}
            if all(p.doc_type != doc_type for p in src.produces):
                update["produces"] = [*src.produces, Produces(doc_type=doc_type)]
            if src.last_tax_year is not None and src.last_tax_year < year:
                update["last_tax_year"] = None  # it's back
            if update:
                store.put(src.model_copy(update=update), rev)
        except RecordNotFound:
            store.put(
                Source(
                    id=source_id,
                    institution=inst_id,
                    name=source,
                    owner=owner,
                    produces=[Produces(doc_type=doc_type)],
                ),
                None,
            )
        store.put(
            Expectation(
                id=exp_id,
                year=year,
                source=source_id,
                doc_type=doc_type,
                amount=amount,
                comment=comment.strip(),
            ),
            None,
        )
    return exp_id


def delete_row(store: RecordStore, exp_id: str, actor: str) -> None:
    """Delete a checklist row. Refused when documents are attached; the source stays."""
    exp, rev = store.get("expectation", exp_id)
    if exp.evidence:
        raise ValueError(
            f"{exp_id} has {len(exp.evidence)} attached file(s); mark it not applicable instead"
        )
    with store.operation(actor, f"row delete {exp_id}"):
        store.delete("expectation", exp_id, rev)


def parse_amount(text: str | None) -> Decimal | None:
    """'$1,234.50' → Decimal('1234.50'); blank → None; anything else is a ValueError."""
    cleaned = re.sub(r"[\s$,]", "", text or "")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{text!r} is not an amount") from None
    if not value.is_finite() or abs(value) >= Decimal("1e12"):
        raise ValueError(f"{text!r} is not an amount")
    return value.quantize(Decimal("0.01"))


_UNSET: object = object()


def edit_row(
    store: RecordStore,
    exp_id: str,
    actor: str,
    amount: Decimal | None | object = _UNSET,
    comment: str | object = _UNSET,
) -> None:
    """Change a row's amount and/or comment; each change is recorded as an "edited" event."""
    exp, rev = store.get("expectation", exp_id)
    edited = row_edited(exp, amount, comment)
    if edited is None:
        return
    changed = [k for k in ("amount", "comment") if getattr(edited, k) != getattr(exp, k)]
    with store.operation(actor, f"row edit {exp_id} ({', '.join(changed)})"):
        store.put(edited, rev)


def row_edited(
    exp: Expectation, amount: Decimal | None | object = _UNSET, comment: str | object = _UNSET
) -> Expectation | None:
    """The row with a new amount and/or comment and an "edited" event each; None if unchanged.
    For callers that write it inside their own operation."""
    update: dict = {}
    events = list(exp.events)
    now = datetime.now(UTC)
    if amount is not _UNSET and amount != exp.amount:
        update["amount"] = amount
        events.append(Event(at=now, type="edited", text="amount"))
    if comment is not _UNSET and str(comment).strip() != exp.comment:
        update["comment"] = str(comment).strip()[:500]
        events.append(Event(at=now, type="edited", text="comment"))
    return exp.model_copy(update={**update, "events": events}) if update else None
