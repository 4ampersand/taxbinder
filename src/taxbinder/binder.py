"""Your binder: for each year, kinds of document across and issuers down; the home page's year
cards are drawn from it (`web/binder_views.py`).

Built from what Across the years already works out (`matrix.rows`): each square is one issuer's
documents of one kind in one year, with the referenced box's amount and the same marks. An
issuer is an institution, told apart by its EIN. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from taxbinder import matrix, perf
from taxbinder.report import DOC_LABELS
from taxbinder.store import RecordStore

# A square's status, strongest first when an issuer has several accounts of one kind.
STATUSES = ("missing", "gap", "to_come", "in", "na")


@dataclass
class Square:
    status: str  # in · to_come · missing (never arrived) · gap (came last year, not on this
    #              year's list) · na (not that year)
    amount: Decimal | None = None
    docs: list[str] = field(default_factory=list)
    ein_read: bool = True  # False: a document here whose issuer's EIN wasn't read
    rows: list[str] = field(default_factory=list)  # the checklist rows behind it


@dataclass
class Issuer:
    id: str
    name: str
    ein: str  # masked, "…4821", or ""
    kinds: list[str] = field(default_factory=list)


@dataclass
class Binder:
    years: list[int]  # oldest first
    kinds: list[tuple[str, str]]  # (doc type, label), in DOC_LABELS order
    issuers: list[Issuer]  # grouped by their first kind, then by name: a staircase
    squares: dict[tuple[str, str, int], Square]  # (issuer, doc type, year)
    usual: dict[str, str] = field(default_factory=dict)  # issuer → when it usually sends, "Mar 15"

    def square(self, issuer: str, kind: str, year: int) -> Square | None:
        return self.squares.get((issuer, kind, year))


def _merge(a: Square, b: Square) -> Square:
    status = min(a.status, b.status, key=STATUSES.index)
    amounts = [x for x in (a.amount, b.amount) if x is not None]
    return Square(status, sum(amounts) if amounts else None, a.docs + b.docs,
                  a.ein_read and b.ein_read, a.rows + b.rows)  # fmt: skip


@perf.timed("binder.build")
def build(store: RecordStore, today: date | None = None) -> Binder:
    from taxbinder.doctypes import labels

    names = labels(store)
    institutions = {i.id: i for i in store.list("institution")}
    expectations = {(e.year, e.source, e.doc_type): e for e in store.list("expectation")}
    docs = {d.id: d for d in store.list("document")}
    years = matrix.years(store)
    squares: dict[tuple[str, str, int], Square] = {}
    ended: dict[tuple[str, str], bool] = {}  # every account of this issuer and kind has ended
    for row in matrix.rows(store, today, every=True):
        issuer = row.source.institution
        last = row.source.last_tax_year
        stopped = last is not None and years and last < years[-1]
        ended[(issuer, row.doc_type)] = ended.get((issuer, row.doc_type), True) and bool(stopped)
        for year, cell in row.cells.items():
            if cell.amount is None and cell.mark in ("", "none"):
                continue
            status = "in" if cell.amount is not None or cell.mark == "here" else cell.mark
            e = expectations.get((year, row.source.id, row.doc_type))
            linked = [x.doc for x in e.evidence if x.doc in docs] if e else []
            ein_read = all(
                (c := docs[d].classification) is not None and c.issuer_tin for d in linked
            )
            sq = Square(status, cell.amount, linked, ein_read, [e.id] if e else [])
            key = (issuer, row.doc_type, year)
            squares[key] = _merge(squares[key], sq) if key in squares else sq
    if len(years) >= 2:  # came last year, not on this year's list: a gap, not just nothing
        last, before = years[-1], years[-2]
        for (issuer, kind, year), sq in list(squares.items()):
            if (year == before and sq.status == "in" and (issuer, kind, last) not in squares
                    and not ended.get((issuer, kind))):
                squares[(issuer, kind, last)] = Square("gap")
    order = list(DOC_LABELS) + sorted(set(names) - set(DOC_LABELS))
    used = sorted({k for _, k, _ in squares}, key=lambda k: order.index(k) if k in order else 999)
    issuers = []
    for inst_id in {i for i, _, _ in squares}:
        inst = institutions.get(inst_id)
        kinds = [k for k in used if any((inst_id, k, y) in squares for y in years)]
        ein = "…" + inst.tin[-4:] if inst and inst.tin else ""
        issuers.append(Issuer(inst_id, inst.name if inst else inst_id, ein, kinds))
    issuers.sort(key=lambda i: (used.index(i.kinds[0]) if i.kinds else 999, i.name.lower()))
    usual = {
        i.id: date(2000, i.typical_release.month, i.typical_release.day).strftime("%b %-d")
        for i in institutions.values() if i.typical_release
    }  # fmt: skip
    return Binder(years, [(k, names.get(k, k)) for k in used], issuers, squares, usual)


STATUS_TEXT = {
    "in": "in the binder",
    "to_come": "to come",
    "missing": "never arrived",
    "gap": "came last year, not on this year's list",
    "na": "not that year",
}


def short(amount: Decimal | None) -> str:
    """An amount small enough for a square: $412, $7.4k, $162k, $1.2M."""
    if amount is None:
        return ""
    a, sign = abs(amount), "−" if amount < 0 else ""
    if a < 1000:
        return f"{sign}${a:,.0f}"
    if a < 10_000:
        return f"{sign}${a / 1000:.1f}k".replace(".0k", "k")
    if a < 1_000_000:
        return f"{sign}${a / 1000:.0f}k"
    return f"{sign}${a / 1_000_000:.1f}M"


@dataclass
class YearSide:
    year: int
    got: int
    to_come: int
    missing: int
    open: list[tuple[str, str, str, str]]  # (issuer, kind label, status, the square's key)


def year_sides(c: Binder) -> list[YearSide]:
    """What each year's side panel says: counts, and the squares still open."""
    names = {i.id: i for i in c.issuers}
    labels = dict(c.kinds)
    years = []
    for y in c.years:
        here = [(k, sq) for k, sq in c.squares.items() if k[2] == y]
        open_ = [
            (names[i].name, labels[kind], STATUS_TEXT[sq.status] + (
                f", usually by {c.usual[i]}" if sq.status == "to_come" and i in c.usual else ""),
             f"{i}|{kind}|{y}")
            for (i, kind, _), sq in sorted(here, key=lambda x: STATUSES.index(x[1].status))
            if sq.status in ("missing", "gap", "to_come")
        ]  # fmt: skip
        status = [sq.status for _, sq in here]
        years.append(YearSide(y, status.count("in"), status.count("to_come"),
                              status.count("missing") + status.count("gap"), open_))  # fmt: skip
    return years

