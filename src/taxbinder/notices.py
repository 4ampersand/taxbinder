"""Noticed for you: notes worked out from the binder's own years by rules, never
by the model. Each is answered with one click, an undoable change, and leads to its square on
the year's card. Set aside, a note doesn't come back (`AppSettings.dismissed_notices`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from taxbinder import settings
from taxbinder.binder import Binder
from taxbinder.models.expectation import Expectation
from taxbinder.report import DOC_LABELS
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.years import expectation_id


@dataclass
class Notice:
    id: str
    text: str
    actions: list[tuple[str, str]]  # (label, action)
    face: str = "years"  # where it leads: "years" (a year) or "docs" (a kind)
    at: str = ""  # that year or kind
    extra: list[str] = field(default_factory=list)
    # same EIN: each name (institution id, name) with its documents (document id, what it is)
    names: list[tuple[str, str, list[tuple[str, str]]]] = field(default_factory=list)


def _sources(store: RecordStore, issuer: str, kind: str, year: int) -> list[str]:
    """The issuer's accounts with a row of this kind in that year."""
    mine = {s.id for s in store.list("source") if s.institution == issuer}
    return [e.source for e in store.list("expectation", year)
            if e.source in mine and e.doc_type == kind]  # fmt: skip


def notices(store: RecordStore, c: Binder) -> list[Notice]:
    from taxbinder.institutions import same_ein

    gone = set(settings.load(store).dismissed_notices)
    names = {i.id: i.name for i in c.issuers}
    labels = dict(c.kinds)
    out = []
    for (issuer, kind, year), sq in sorted(c.squares.items()):
        if sq.status == "gap":
            out.append(Notice(
                f"gap:{issuer}:{kind}:{year}",
                f"{names[issuer]}'s {labels[kind]} came in {year - 1}, but it isn't on {year}'s "
                "list. Did the account close?",
                [(f"Expect it in {year}", "add"), ("The account closed", "stopped")],
                "docs", kind,
            ))  # fmt: skip
    corrected: dict[str, set[int]] = {}
    for d in store.list("document"):
        k = d.classification
        if not (d.version.is_corrected and k and k.issuer and k.tax_year):
            continue
        if (k.doc_type or "").startswith("k1"):
            corrected.setdefault(k.issuer, set()).add(k.tax_year)
    latest = c.years[-1] if c.years else None
    for issuer, years in sorted(corrected.items()):
        if latest is None or issuer not in names or latest in years:
            continue
        when = " and ".join(str(y) for y in sorted(years))
        out.append(Notice(
            f"corrected:{issuer}:{latest}",
            f"{names[issuer]} sent a corrected K-1 for {when}. You may want to wait for its "
            f"{latest} K-1 to settle before handing off.",
            [("Got it", "dismiss")], "years", str(latest),
        ))  # fmt: skip
    docs: dict[str, list[tuple[str, str]]] = {}
    for d in sorted(store.list("document"), key=lambda d: d.id):
        if (k := d.classification) and k.issuer:
            what = DOC_LABELS.get(k.doc_type or "", k.doc_type or "Document")
            docs.setdefault(k.issuer, []).append(
                (d.id, f"{what} · {k.tax_year}" if k.tax_year else what))
    for group in same_ein(store.list("institution")):
        out.append(Notice(
            "ein:" + ":".join(i.id for i in group),
            f"{len(group)} names have the same EIN (…{(group[0].tin or '')[-4:]}): one sender, "
            "renamed? Look at their documents, then keep the right name.",
            [("They're different", "dismiss")],
            extra=[i.id for i in group],
            names=[(i.id, i.name, docs.get(i.id, [])) for i in group],
        ))  # fmt: skip
    return [n for n in out if n.id not in gone]


def answer(store: RecordStore, notice_id: str, action: str, actor: str) -> None:
    """One click on a note, as one undoable change."""
    from taxbinder import check

    kind_of, *parts = notice_id.split(":")
    if action == "dismiss":
        current = settings.load(store)
        try:
            rev = store.get("settings", "settings")[1]
        except RecordNotFound:
            rev = None
        with store.operation(actor, "note: set aside"):
            store.put(current.model_copy(
                update={"dismissed_notices": [*current.dismissed_notices, notice_id]}), rev)
        return
    if kind_of == "gap" and action in ("add", "stopped"):
        issuer, kind, year = parts[0], parts[1], int(parts[2])
        sources = _sources(store, issuer, kind, year - 1)
        if not sources:
            raise ValueError("nothing to do: that row is gone")
        with store.operation(actor, f"note: {issuer} {kind} " +
                             (f"expected in {year}" if action == "add" else "ended")):
            for sid in sources:
                if action == "add":
                    eid = expectation_id(year, sid, kind)
                    try:
                        store.get("expectation", eid)
                    except RecordNotFound:
                        store.put(Expectation(id=eid, year=year, source=sid, doc_type=kind), None)
                else:
                    src, rev = store.get("source", sid)
                    store.put(src.model_copy(update={"last_tax_year": year - 1}), rev)
        return
    if kind_of == "ein" and (action == "join" or action.startswith("keep:")):
        keep = parts[0] if action == "join" else action.removeprefix("keep:")
        if keep not in parts:
            raise ValueError("choose one of the note's names")
        with store.operation(actor, f"note: one sender, kept as {keep}"):
            for other in parts:
                if other != keep:
                    check.merge(store, other, keep, actor)
        return
    raise ValueError(f"unknown answer {action!r}")
