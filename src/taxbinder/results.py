"""Results: three plain answers for the year (still to come, needs attention,
ready for your preparer?) and the few questions about what was paid that aren't documents.

A question is asked only with a reason (last year's return, or last year's payments and gifts
in the binder) and never when this year already answers it (a payment added; a 1098 showing
property tax paid from escrow). At most 3 at a time. "No" and "Not sure" are remembered for the
year (`YearConfig.asked`); "Yes" goes to where it's added."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from taxbinder.models import YearConfig
from taxbinder.models.document import ReturnRead
from taxbinder.store import RecordNotFound, RecordStore

MAX_ASKED = 3
ANSWERS = ("no", "unsure")


@dataclass
class Ask:
    kind: str
    text: str
    why: str
    yes: str  # the Yes button's words
    href: str  # where Yes adds it
    no: str = "No"


@dataclass
class Answers:
    to_come: list[tuple[str, str]] = field(default_factory=list)  # (what, "often March")
    attention: list[str] = field(default_factory=list)  # in words
    ready: bool = False
    why: str = ""
    asks: list[Ask] = field(default_factory=list)
    known: list[str] = field(default_factory=list)  # answered by a document: nothing to ask


def _config(store: RecordStore, year: int) -> tuple[YearConfig, str | None]:
    try:
        return store.get("year", str(year))
    except RecordNotFound:
        return YearConfig(year=year), None


def _last_return(store: RecordStore, year: int) -> ReturnRead | None:
    """What last year's 1040 says (not a draft)."""
    for d in store.list("document"):
        r = d.tax_return
        c = d.classification
        if r and r.read and r.status != "draft" and c and c.tax_year == year - 1:
            return r.read
    return None


def _escrowed(store: RecordStore, year: int) -> tuple[Decimal, str] | None:
    """Property tax a 1098 for the year shows paid from escrow (box 10), and the lender."""
    from taxbinder.extract.figures import read_boxes
    from taxbinder.ingest.inbox import scanned_pages

    names = {i.id: i.name for i in store.list("institution")}
    for d in store.list("document"):
        c = d.classification
        if not c or c.doc_type != "1098" or c.tax_year != year:
            continue
        try:
            got = read_boxes(store.blobs.get(d.sha256), "1098", ["10"], scanned_pages(store, d))
        except Exception:  # noqa: BLE001 - an unreadable file answers nothing
            continue
        if "10" in got and got["10"].value > 0:
            return got["10"].value, names.get(c.issuer or "", "your lender")
    return None


def _estimated(c: Any) -> bool:  # noqa: ANN401
    return c.category == "tax_payment" and c.payment.kind.startswith("estimated")


def questions(store: RecordStore, year: int) -> tuple[list[Ask], list[str]]:
    """The questions to ask now (at most 3), and the lines a document already answers."""
    asked = _config(store, year)[0].asked
    paid = set(r.paid) if (r := _last_return(store, year)) else set()
    now, before = store.list("claim", year), store.list("claim", year - 1)

    def any_of(claims: list, test: Any) -> bool:  # noqa: ANN401
        return any(test(c) for c in claims)

    last = year - 1
    out: list[Ask] = []
    known: list[str] = []
    # Estimated tax payments.
    why = (f"Your {last} return shows estimated tax payments." if "estimated" in paid else
           f"You made estimated payments for {last}." if any_of(before, _estimated) else "")
    if why and not any_of(now, _estimated):
        out.append(Ask("estimated", f"Did you make estimated tax payments for {year}?", why,
                       "Yes, add them", "/payments?add=tax_payment"))
    # Property tax: a 1098 showing it paid from escrow answers it.
    if escrow := _escrowed(store, year):
        amount, lender = escrow
        known.append(f"Property tax {year}: ${amount:,.0f}, from your {lender} mortgage statement")
    else:
        is_tax = lambda c: c.category == "property_tax"  # noqa: E731
        why = (f"Your {last} return deducted real estate taxes." if "property_tax" in paid else
               f"You paid property tax in {last}." if any_of(before, is_tax) else "")
        if why and not any_of(now, is_tax):
            out.append(Ask("property_tax", f"Did you pay property tax in {year}?", why,
                           "Yes, add it", "/payments?add=property_tax"))
    # A vehicle fee, where part of it is a tax.
    is_fee = lambda c: c.category == "vehicle_fee"  # noqa: E731
    why = (f"Your {last} return deducted personal property taxes; in some states part of a "
           "vehicle registration fee is one." if "vehicle" in paid else
           f"You paid a vehicle fee in {last}." if any_of(before, is_fee) else "")
    if why and not any_of(now, is_fee):
        out.append(Ask("vehicle", f"Did you pay a vehicle registration fee in {year}?", why,
                       "Yes, add it", "/payments?add=vehicle_fee"))
    # Gifts to charity: some found already is no answer (there may be more).
    is_gift = lambda c: c.category == "donation"  # noqa: E731
    why = (f"Your {last} return listed gifts to charity." if "gifts" in paid else
           f"You gave to charity in {last}." if any_of(before, is_gift) else "")
    if why:
        found = sum(map(is_gift, now))
        if found:
            out.append(Ask("gifts", f"Did you give anything else to charity in {year}?",
                           f"{why} {found} gift{'s' if found != 1 else ''} added so far.",
                           "Yes, add more", "/donations?add=1", no="No, that's all"))
        else:
            out.append(Ask("gifts", f"Did you give to charity in {year}?", why, "Yes, add them",
                           "/donations?add=1"))  # fmt: skip
    return [a for a in out if a.kind not in asked][:MAX_ASKED], known  # fmt: skip


def answer(store: RecordStore, year: int, kind: str, value: str, actor: str) -> None:
    """No or Not sure, remembered for the year, or "" to take the answer back (each one
    undoable change)."""
    if value not in (*ANSWERS, ""):
        raise ValueError("an answer is no or unsure")
    if kind not in PAID:
        raise ValueError(f"no question {kind!r}")
    config, rev = _config(store, year)
    words = {"no": "no", "unsure": "not sure, ask the preparer", "": "asked again"}[value]
    asked = {k: v for k, v in config.asked.items() if k != kind} | ({kind: value} if value else {})
    with store.operation(actor, f"results {year}: {kind.replace('_', ' ')}: {words}"):
        store.put(config.model_copy(update={"asked": asked}), rev)


@dataclass
class Mail:
    """One thing that doesn't come in the mail, and where it stands for the year."""

    kind: str  # as in `questions` and `answer`
    name: str
    added: int  # this year's, in the binder
    total: Decimal  # their amounts added up
    before: str  # last year's, in words ("" when nothing's known)
    answer: str  # "no", "unsure" or ""
    known: str  # a document answers it ("" when none does)
    href: str  # where Add takes you


MAIL = [  # kind, name, the claims it counts, where they're added
    ("gifts", "Gifts to charity", lambda c: c.category == "donation", "/donations?add=1"),
    ("estimated", "Estimated tax payments", _estimated, "/payments?add=tax_payment"),
    ("property_tax", "Property tax", lambda c: c.category == "property_tax",
     "/payments?add=property_tax"),
    ("vehicle", "Vehicle registration (DMV) fees", lambda c: c.category == "vehicle_fee",
     "/payments?add=vehicle_fee"),
    ("foreign", "Accounts abroad (FBAR)", lambda c: c.category == "fbar", "/foreign?add=1"),
]  # fmt: skip
PAID = {"gifts": "gifts", "estimated": "estimated", "property_tax": "property_tax",
        "vehicle": "vehicle",
        "foreign": "foreign"}  # fmt: skip  # `ReturnRead.paid`'s words for each


def mail(store: RecordStore, year: int) -> list[Mail]:
    """Every common thing paid that no form reports, each with this year's so far, last
    year's, and the answer given: so nothing's missed because no envelope came."""
    asked = _config(store, year)[0].asked
    paid = set(r.paid) if (r := _last_return(store, year)) else set()
    now, before = store.list("claim", year), store.list("claim", year - 1)
    escrow = _escrowed(store, year)
    out = []
    for kind, name, test, href in MAIL:
        mine, theirs = [c for c in now if test(c)], [c for c in before if test(c)]
        last = (f"{len(theirs)} in {year - 1}" if theirs else
                f"on your {year - 1} return" if PAID[kind] in paid else "")  # fmt: skip
        known = ""
        if kind == "property_tax" and escrow:
            known = f"${escrow[0]:,.0f} paid from escrow, on your {escrow[1]} mortgage statement"
        total = sum((c.amount for c in mine if c.amount is not None), Decimal(0))
        out.append(Mail(kind, name, len(mine), total, last, asked.get(kind, ""), known, href))
    return out


def answers(store: RecordStore, year: int, today: date, rows: list, status: Any) -> Answers:  # noqa: ANN401
    """The three answers, from the checklist rows (views.checklist_rows) and the checks."""
    to_come = [r for r in rows if r.status[0] in ("to_get", "received")]
    out = Answers(to_come=[(f"{r.institution or r.source} · {r.doc_label}", r.usually)
                           for r in to_come])  # fmt: skip
    if status.checked:
        found = [f for key in ("missing", "conflicts", "attention") for f in status.findings[key]]
        out.attention = [f.message for f in found if f.severity in ("error", "warning")]
    out.asks, out.known = questions(store, year)
    reasons = []
    if to_come:
        reasons.append(f"{len(to_come)} document{'s' if len(to_come) != 1 else ''} to come")
    if out.attention:
        n = len(out.attention)
        reasons.append(f"{n} thing{'s' if n != 1 else ''} to look at")
    if out.asks:
        reasons.append(f"{len(out.asks)} question{'s' if len(out.asks) != 1 else ''} to answer")
    out.ready = not reasons and bool(rows)
    out.why = (", ".join(reasons) + "." if reasons else
               "Everything expected is here." if rows else "Nothing expected yet: add documents.")
    return out
