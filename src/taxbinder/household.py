"""The household: worked out from last year's return and the names printed on
the documents, shown as "Is this right?", and saved only when a person confirms it.

Once people are in the vault they're what's shown; a guess never replaces them. The taxpayer is
`primary` and the spouse `spouse`, as the rest of the app expects. Only the last 4 of an SSN
are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from taxbinder.models import Taxpayer
from taxbinder.models.year import YearConfig
from taxbinder.store import RecordNotFound, RecordStore

ROLES = ("taxpayer", "spouse", "dependent")
FIXED_IDS = {"taxpayer": "primary", "spouse": "spouse"}


@dataclass
class Person:
    name: str
    role: str | None = None  # taxpayer, spouse, dependent; None: a trust, or not said yet
    kind: str = "individual"  # or trust
    last4: str | None = None
    id: str | None = None  # the vault's id, once confirmed


@dataclass
class Household:
    people: list[Person] = field(default_factory=list)
    filing_status: str | None = None
    state: str | None = None
    found_in: str = "nothing"  # confirmed | return | documents | nothing
    return_year: int | None = None  # the return it was read from
    documents: int = 0  # how many documents the names came from

    @property
    def confirmed(self) -> bool:
        return self.found_in == "confirmed"

    @property
    def complete(self) -> bool:
        """Enough to save: one taxpayer, and everyone individual has a role."""
        roles = [p.role for p in self.people if p.kind == "individual"]
        return roles.count("taxpayer") == 1 and None not in roles


def _year(store: RecordStore, year: int) -> YearConfig | None:
    try:
        return store.get("year", str(year))[0]
    except RecordNotFound:
        return None


def _latest_1040(store: RecordStore, year: int):  # noqa: ANN202 - (tax year, ReturnRead) or None
    found = [
        (d.classification.tax_year, d.tax_return.read)
        for d in store.list("document")
        if d.tax_return
        and d.tax_return.read
        and d.classification
        and d.classification.tax_year
        and d.classification.tax_year <= year
    ]
    return max(found, key=lambda f: f[0]) if found else None


def _printed_names(store: RecordStore) -> tuple[list[Person], int]:
    """The recipients printed on the documents, one person each ("A & B" is two)."""
    from taxbinder.whose import TRUST

    people: dict[str, Person] = {}
    docs = 0
    for d in sorted(store.list("document"), key=lambda d: d.acquired.at):  # as they came
        printed = d.recipient.printed if d.recipient else None
        if not printed:
            continue
        docs += 1
        if TRUST.search(printed):
            parts = [printed]
        else:
            parts = re.split(r"\s*(?:&|\band\b|/|;)\s*", printed, flags=re.IGNORECASE)
            surname = parts[-1].split()[-1] if parts[-1].split() else ""
            parts = [p if len(p.split()) > 1 else f"{p} {surname}" for p in parts if p.strip()]
        for part in parts:
            name = " ".join(w.capitalize() if w.isupper() else w for w in part.split())
            kind = "trust" if TRUST.search(name) else "individual"
            people.setdefault(name.lower(), Person(name=name, kind=kind))
    return list(people.values()), docs


def worked_out(store: RecordStore, year: int) -> Household:
    """What the app shows for `year`: the confirmed household, else last year's return, else
    the names on the documents."""
    config = _year(store, year) or _year(store, year - 1)
    known = store.list("taxpayer")
    if known:
        people = [
            Person(name=t.name, role=t.role, kind=t.kind, last4=t.last4, id=t.id) for t in known
        ]
        order = {r: i for i, r in enumerate(ROLES)}
        people.sort(key=lambda p: (p.kind != "individual", order.get(p.role or "", 3)))
        states = config.resident_states if config else []
        return Household(
            people,
            config.filing_status if config else None,
            states[0] if states else None,
            "confirmed",
        )
    if latest := _latest_1040(store, year):
        return_year, read = latest
        people = [Person(name=p.name, role=p.role, last4=p.last4) for p in read.people]
        return Household(people, read.filing_status, read.state, "return", return_year)
    people, docs = _printed_names(store)
    return Household(people, found_in="documents" if people else "nothing", documents=docs)


def _slug(store: RecordStore, person: Person, taken: set[str]) -> str:
    from taxbinder.institutions import name_key
    from taxbinder.slugs import slugify

    if person.id:
        return person.id
    if person.role in FIXED_IDS:
        return FIXED_IDS[person.role]
    base = slugify(name_key(person.name) if person.kind != "individual" else person.name)
    slug, n = base or "person", 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def confirm(store: RecordStore, household: Household, year: int, actor: str) -> None:
    """Save the household as a person confirmed it: the people, the filing status and state for
    the return's year and the year after (else `year`), and the documents nobody matched read
    again against the people. One change, undoable."""
    from taxbinder.whose import recipient_of

    if not household.complete:
        raise ValueError("say who the taxpayer is, and who everyone else is")
    if household.state and not re.fullmatch(r"[A-Z]{2}", household.state):
        raise ValueError("a state is two letters, e.g. NY")
    current = {t.id: t for t in store.list("taxpayer")}
    taken = set(current)
    records = []
    for p in household.people:
        if not p.name.strip():
            continue
        slug = _slug(store, p, taken)
        taken.add(slug)
        old = current.get(slug)
        update = {
            "name": p.name.strip(),
            "kind": p.kind,
            "role": p.role if p.kind == "individual" else None,
            "last4": p.last4 or (old.last4 if old else None),
        }
        records.append(old.model_copy(update=update) if old else Taxpayer(id=slug, **update))
    gone = set(current) - {t.id for t in records}
    used = {s.owner for s in store.list("source")} | {
        t for d in store.list("document") if d.recipient for t in d.recipient.taxpayers
    }
    if busy := sorted(gone & used):
        names = ", ".join(current[i].name for i in busy)
        raise ValueError(f"{names} has rows or documents; change the name instead of removing")
    years = [household.return_year, household.return_year + 1] if household.return_year else [year]
    with store.operation(actor, f"household: {len(records)} people"):
        for t in records:
            rev = store.get("taxpayer", t.id)[1] if t.id in current else None
            if t.id not in current or t != current[t.id]:
                store.put(t, rev)
        for slug in gone:
            store.delete("taxpayer", slug, store.get("taxpayer", slug)[1])
        for y in years:
            try:
                config, rev = store.get("year", str(y))
            except RecordNotFound:
                config, rev = YearConfig(year=y), None
            update = {"filing_status": household.filing_status}
            if household.state:
                others = [s for s in config.resident_states if s != household.state]
                update["resident_states"] = [household.state, *others]
            new = config.model_copy(update=update)
            if new != config or rev is None:
                store.put(new, rev)
        for d in store.list("document"):  # "Whose? …" documents, read again
            r = d.recipient
            if r and r.printed and (r.ask or not r.taxpayers):
                again = recipient_of(r.printed, None, records)
                if again.taxpayers and not again.ask:
                    rev = store.get("document", d.id)[1]
                    store.put(d.model_copy(update={"recipient": again}), rev)
