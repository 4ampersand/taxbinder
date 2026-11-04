"""Across the years: key figures across all years, one row per document
(source + type).

A row has a referenced box, its form's usual box unless the person picked another; each year
shows that box's amount: typed, else read from the year's document (a read value never replaces
a typed one), else a mark (to come, never arrived, not that year, not expected). A row may have
several boxes ("1, 5": the preparer's numbers), shown "4,535 / 9,764". The first box's value is
the year's checklist row Amount when that row exists (so the Checklist and Across the years show the
same number); everything else lives in `years/<year>/figures.yaml`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from taxbinder import perf
from taxbinder.models.matrix import (
    Dismissed,
    Figure,
    FigureSet,
    MatrixLayout,
    MatrixRow,
    Suggestion,
)
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.years import parse_amount, row_edited, years_in_vault

SEP = " / "
BIG_SHARE, BIG_DOLLARS = Decimal("0.25"), Decimal(500)  # a big change: both, or a change of sign
MARKS = {  # a year without an amount
    "here": ("here", "Here; its box couldn't be read"),
    "to_come": ("○", "To come"),
    "missing": ("✕", "Never arrived"),
    "na": ("—", "Not that year"),
    "none": ("·", "Not expected that year"),
}


def whole(v: Decimal | None) -> str:
    """Whole dollars: 4535.49 → '4,535'; -71880 → '(71,880)'; None → '—'."""
    if v is None:
        return "—"
    n = int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"({abs(n):,})" if n < 0 else f"{n:,}"


def parse_cell(text: str, boxes: int) -> list[Decimal | None]:
    """'4535 / 9764' → two values; '—' or blank → None; fewer parts than boxes are None."""
    parts = [p.strip() for p in re.split(r"\s*/\s*", text.strip())] if text.strip() else []
    if len(parts) > max(boxes, 1):
        raise ValueError(f"this row has {max(boxes, 1)} box(es); separate values with ' / '")
    values: list[Decimal | None] = []
    for p in parts:
        p = p.replace("—", "").replace("–", "")
        negative = p.startswith("(") and p.endswith(")")
        v = parse_amount(p.strip("()"))
        values.append(-v if negative and v is not None else v)
    return values + [None] * (max(boxes, 1) - len(values))


def boxes_of(text: str) -> list[str]:
    return [b.strip() for b in text.split(",") if b.strip()]


@dataclass
class Cell:
    values: list[Decimal | None]
    suggested: list[Decimal | None] | None = None  # read from the year's PDF, not yet accepted
    doc: str = ""  # the document read from, else the year's document for the row
    pages: list[int | None] | None = None
    scanned: bool = False  # the suggestion was read from a scan
    checks: list[str] = field(default_factory=list)  # the form's checks the values read fail
    mark: str = ""  # without an amount: a MARKS key

    def _shown(self) -> list[Decimal | None]:
        if not self.suggested:
            return self.values
        return [
            v if v is not None else s for v, s in zip(self.values, self.suggested, strict=False)
        ]

    @property
    def pending(self) -> bool:
        """Some box is empty and the PDF has a value for it."""
        return bool(self.suggested) and any(
            v is None and s is not None for v, s in zip(self.values, self.suggested, strict=False)
        )

    @property
    def conflict(self) -> bool:
        """A value was typed and the PDF says something else (to the dollar)."""
        return bool(self.suggested) and any(
            v is not None and s is not None and round(v) != round(s)
            for v, s in zip(self.values, self.suggested, strict=False)
        )

    @property
    def text(self) -> str:
        shown = self._shown()
        return "" if all(v is None for v in shown) else SEP.join(map(whole, shown))

    @property
    def parts(self) -> list[tuple[str, bool]]:
        """Each box's text and whether it's negative (shown in red)."""
        shown = self._shown()
        if all(v is None for v in shown):
            return []
        return [(whole(v), v is not None and v < 0) for v in shown]

    def part(self, i: int) -> tuple[str, bool] | None:
        """Box i's text and whether it's negative; None when it has nothing (a sub-row's cell)."""
        shown = self._shown()
        v = shown[i] if i < len(shown) else None
        return None if v is None else (whole(v), v < 0)

    def shown_at(self, i: int) -> Decimal | None:
        """Box i as shown: typed, else read from the document."""
        shown = self._shown()
        return shown[i] if i < len(shown) else None

    def raw_at(self, i: int) -> str:
        """Box i's typed value, for editing it alone."""
        v = self.values[i] if i < len(self.values) else None
        return "" if v is None else str(v)

    def conflict_at(self, i: int) -> bool:
        """Box i was typed and the PDF says something else."""
        v = self.values[i] if i < len(self.values) else None
        s = self.suggested[i] if self.suggested and i < len(self.suggested) else None
        return v is not None and s is not None and round(v) != round(s)

    def doubt_at(self, i: int) -> bool:
        """Box i shows a value read from the document that needs a look (it differs from what's
        typed, or the reading fails a check, or came from a scan)."""
        v = self.values[i] if i < len(self.values) else None
        s = self.suggested[i] if self.suggested and i < len(self.suggested) else None
        read = v is None and s is not None
        return self.conflict_at(i) or (read and bool(self.checks or self.scanned))

    @property
    def raw(self) -> str:
        return SEP.join("" if v is None else str(v) for v in self.values).strip(" /")

    @property
    def first(self) -> Decimal | None:
        return self.values[0] if self.values else None

    @property
    def amount(self) -> Decimal | None:
        """The referenced box's amount as shown: typed, else read from the document."""
        shown = self._shown()
        return shown[0] if shown else None

    @property
    def page(self) -> int:
        """The page of `doc` the amount was read from (the viewer opens there), else 1."""
        return next((p for p in self.pages or [] if p), 1)

    @property
    def read(self) -> bool:
        """The referenced box shows a value read from the document (nothing typed)."""
        return self.first is None and self.amount is not None

    @property
    def symbol(self) -> tuple[str, str]:
        return MARKS.get(self.mark, ("", ""))


@dataclass
class Row:
    source: Any
    doc_type: str
    boxes: list[str]
    label: str  # short document type, e.g. "K1"
    ident: str  # masked, e.g. "…4567"
    cells: dict[int, Cell]
    own: bool = False  # the box was picked by the person
    usual: str | None = None  # the form's usual box
    options: list[tuple[str, str]] = field(default_factory=list)  # the form's boxes read
    sender: str = ""  # the institution that sends it, e.g. "Brackley Wealth"

    @property
    def account(self) -> str:
        """The account, when it isn't just the sender's name again ("Account …4821")."""
        name = self.source.name
        return "" if not self.sender or name.casefold() == self.sender.casefold() else name

    def big(self, year: int, i: int = 0) -> str:
        """A big change in box i from the year before, in words; '' otherwise (a fact, not
        advice)."""
        cell, prev = self.cells[year], self.cells.get(year - 1)
        now = (cell._shown() + [None] * (i + 1))[i]
        before = (prev._shown() + [None] * (i + 1))[i] if prev else None
        if now is None or before is None:
            return ""
        if now * before < 0:
            what = "a loss" if now < 0 else "income"
            return f"From {'income' if before > 0 else 'a loss'} to {what} vs {year - 1}"
        delta = now - before
        if abs(delta) < BIG_DOLLARS or (before and abs(delta) < BIG_SHARE * abs(before)):
            return ""
        way = "Up" if delta > 0 else "Down"
        share = f" {abs(delta) / abs(before):.0%}" if before else ""
        return f"{way}{share} (${whole(abs(delta))}) vs {year - 1}"

    @property
    def key(self) -> str:
        return f"{self.source.id}|{self.doc_type}"

    @property
    def subs(self) -> list[tuple[int, str, str]]:
        """The boxes after the first, each a sub-row of its own: (index, box, its name)."""
        names = dict(self.options)
        return [(i, b, names.get(b, "")) for i, b in enumerate(self.boxes) if i]

    def hidden_doubt(self, year: int | None = None) -> bool:
        """A sub-row's value needs a look (shown on the row while its sub-rows are closed)."""
        years = [year] if year is not None else list(self.cells)
        return any(self.cells[y].doubt_at(i) for y in years for i, _, _ in self.subs)

    def tip(self, year: int) -> str:
        """Hover text: each box's value and its change from the year before."""
        cell, prev = self.cells[year], self.cells.get(year - 1)
        names = self.boxes or [""]
        lines = []
        if not cell.parts:
            return f"{cell.symbol[1]}\nClick to type an amount"
        if cell.read:
            scan = ": a scan, so check it against the paper" if cell.scanned else ""
            lines.append(f"Read from the {year} document{scan}.")
        if big := self.big(year):
            lines.append(f"Big change: {big}. A fact about the documents, not advice.")
        if cell.conflict:
            pdf = SEP.join(map(whole, cell.suggested or []))
            lines.append(f"The {year} document says {pdf}, not what's typed. ✓ takes it, ✕ keeps "
                         "what's typed.")  # fmt: skip
            if cell.scanned:
                lines.append("Read from a scan: check it against the paper first.")
        if cell.read and cell.checks:
            lines.append(f"Check it against the paper: {'; '.join(cell.checks)}.")
        for i, v in enumerate(cell._shown()):
            label = f"Box {names[i]}" if i < len(names) and names[i] else "Amount"
            line = f"{label}: {whole(v)}"
            before = prev._shown()[i] if prev and i < len(prev.values) else None
            if v is not None and before is not None:
                delta = v - before
                pct = f", {delta / abs(before):+.0%}" if before else ""
                sign = "+" if delta >= 0 else "−"
                line += f" ({sign}{whole(abs(delta))} vs {year - 1}{pct})"
            lines.append(line)
        return "\n".join(lines) + "\nClick to edit"


def _layout(store: RecordStore) -> tuple[MatrixLayout | None, str | None]:
    try:
        return store.get("matrix", "matrix")
    except RecordNotFound:
        return None, None


def _figures(store: RecordStore, year: int) -> tuple[FigureSet, str | None]:
    try:
        return store.get("figures", str(year))
    except RecordNotFound:
        return FigureSet(year=year), None


def _expectation(store: RecordStore, year: int, source: str, doc_type: str) -> Any:  # noqa: ANN401
    return next(
        (
            e
            for e in store.list("expectation", year)
            if e.source == source and e.doc_type == doc_type
        ),
        None,
    )


def masked_id(source: Any) -> str:  # noqa: ANN401
    """The source's reference number (e.g. a partnership's EIN), else its account: last 4."""
    ids = sorted(source.identifiers, key=lambda i: {"reference": 0, "primary": 1}.get(i.kind, 2))
    for i in ids:
        chars = re.sub(r"[^0-9A-Za-z]", "", i.number or "")
        if len(chars) >= 4:
            return "…" + chars[-4:]
    return ""


def default_rows(store: RecordStore) -> list[MatrixRow]:
    """Every document (source + type) expected in any year, or with a figure."""
    seen: dict[tuple[str, str], None] = {}
    for e in sorted(store.list("expectation"), key=lambda e: (e.doc_type, e.source)):
        if e.doc_type not in NO_FIGURE or e.amount is not None:
            seen[(e.source, e.doc_type)] = None
    for fs in store.list("figures"):
        for f in fs.figures:
            seen[(f.source, f.doc_type)] = None
    return [MatrixRow(source=s, doc_type=t) for s, t in seen]


def years(store: RecordStore) -> list[int]:
    """From the first year with data (or the one the person picked) to the latest year."""
    layout, _ = _layout(store)
    known = set(years_in_vault(store)) | {fs.year for fs in store.list("figures")}
    if not known:
        return []
    first = min(known) if layout is None or layout.first_year is None else layout.first_year
    return list(range(min(first, max(known)), max(known) + 1))


def _mark(e: Any, year: int, today: date, here: bool) -> str:  # noqa: ANN401
    """A year without an amount: here, to come, never arrived (after the extended deadline),
    not that year, or not expected."""
    if e is None:
        return "none"
    if e.applicability != "applicable":
        return "na"
    if here or e.acquisition in ("received", "vaulted", "superseded"):
        return "here"
    return "missing" if today > date(year + 1, 10, 15) else "to_come"


@perf.timed("matrix.rows")
def rows(store: RecordStore, today: date | None = None, every: bool = False) -> list[Row]:
    """Across the years' rows; `every`: every document expected in any year, whatever rows the
    person chose to show (your binder)."""
    from taxbinder.doctypes import labels
    from taxbinder.extract.figures import LABELS
    from taxbinder.settings import usual_box

    today = today or date.today()
    layout, _ = _layout(store)
    chosen = layout.rows if layout is not None and not every else default_rows(store)
    if every and layout is not None:  # the person's boxes still count where they set them
        own = {(r.source, r.doc_type): r for r in layout.rows}
        chosen = [own.get((r.source, r.doc_type), r) for r in chosen]
    sources = {s.id: s for s in store.list("source")}
    senders = {i.id: i.name for i in store.list("institution")}
    names = labels(store)
    cols = years(store)
    by_year = {y: _figures(store, y)[0] for y in cols}
    expectations: dict[tuple[int, str, str], Any] = {
        (e.year, e.source, e.doc_type): e for e in store.list("expectation")
    }
    out = []
    usuals: dict[str, str | None] = {}
    for r in chosen:
        source = sources.get(r.source)
        if source is None:
            continue
        if r.doc_type not in usuals:
            usuals[r.doc_type] = usual_box(store, r.doc_type)
        usual = usuals[r.doc_type]
        boxes = r.boxes or suggest_boxes(store, r.source, r.doc_type, chosen)  # as a new row
        n = max(len(boxes), 1)
        cells = {}
        for y in cols:
            fig = next(
                (f for f in by_year[y].figures if (f.source, f.doc_type) == (r.source, r.doc_type)),
                None,
            )
            values = list(fig.values if fig else []) + [None] * n
            values = values[:n]
            e = expectations.get((y, r.source, r.doc_type))
            if e is not None:
                values[0] = e.amount
            sug = next(
                (
                    x
                    for x in by_year[y].suggested
                    if (x.source, x.doc_type) == (r.source, r.doc_type)
                ),
                None,
            )
            doc = _document(store, e) if e is not None else None
            cell = Cell(values, doc=doc or "")
            if sug is not None and sug.boxes not in ([], boxes):
                sug = None  # read for another box
            if sug is not None and len(sug.values) == n:
                cell.suggested, cell.doc, cell.pages = list(sug.values), sug.doc, list(sug.pages)
                cell.scanned, cell.checks = sug.scanned, list(sug.checks)
            if cell.suggested and not (cell.pending or cell.conflict):
                cell.suggested = None  # nothing left to decide
            if not cell.parts:
                cell.mark = _mark(e, y, today, doc is not None)
            cells[y] = cell
        out.append(
            Row(
                source,
                r.doc_type,
                boxes,
                names.get(r.doc_type, r.doc_type),
                masked_id(source),
                cells,
                r.own,
                usual,
                list(LABELS.get(r.doc_type, {}).items()),
                senders.get(source.institution, source.name),
            )
        )
    return out


def set_cell(
    store: RecordStore,
    source: str,
    doc_type: str,
    year: int,
    text: str,
    actor: str,
    box: int | None = None,
) -> Cell:
    """Save a year's values for a row: the first box onto that year's checklist row when it
    exists, the rest (or all, without a row) into the year's figures. `box`: only that box's
    value (a sub-row's cell), the others as they are."""
    row = next((r for r in rows(store) if (r.source.id, r.doc_type) == (source, doc_type)), None)
    if row is None:
        raise ValueError("that row isn't in Across the years")
    if not 1990 <= year <= 2100:
        raise ValueError("give a year like 2024")
    if box is not None:
        if not 0 <= box < max(len(row.boxes), 1):
            raise ValueError("that row has no such box")
        values = list(row.cells[year].values) if year in row.cells else []
        values = (values + [None] * max(len(row.boxes), 1))[: max(len(row.boxes), 1)]
        values[box] = parse_cell(text, 1)[0]
        text = SEP.join("" if v is None else str(v) for v in values)
    values = parse_cell(text, len(row.boxes))
    exp = _expectation(store, year, source, doc_type)
    stored = [None if exp is not None and i == 0 else v for i, v in enumerate(values)]
    fs, rev = _figures(store, year)
    others = [f for f in fs.figures if (f.source, f.doc_type) != (source, doc_type)]
    if any(v is not None for v in stored):
        others.append(Figure(source=source, doc_type=doc_type, values=stored))
    edited = row_edited(exp, amount=values[0]) if exp is not None else None
    if edited is None and others == fs.figures:
        return Cell(values)
    with store.operation(actor, f"matrix {year} {row.label} {row.source.name}"):
        if edited is not None:
            store.put(edited, store.get("expectation", exp.id)[1])
        if others != fs.figures:
            store.put(fs.model_copy(update={"figures": others}), rev)
    return Cell(values)


def _save_layout(
    store: RecordStore,
    new_rows: list[MatrixRow],
    actor: str,
    why: str,
    first_year: int | None | object = ...,
) -> None:
    layout, rev = _layout(store)
    base = layout or MatrixLayout(rows=default_rows(store))
    update: dict[str, Any] = {"rows": new_rows}
    if first_year is not ...:
        update["first_year"] = first_year
    with store.operation(actor, f"matrix: {why}"):
        store.put(base.model_copy(update=update), rev)


def current_rows(store: RecordStore) -> list[MatrixRow]:
    layout, _ = _layout(store)
    return list(layout.rows) if layout is not None else default_rows(store)


def add_row(store: RecordStore, source: str, doc_type: str, boxes: str, actor: str) -> None:
    store.get("source", source)  # RecordNotFound for an unknown document
    current = current_rows(store)
    if any((r.source, r.doc_type) == (source, doc_type) for r in current):
        raise ValueError("that document is already a row")
    picked = boxes_of(boxes) or suggest_boxes(store, source, doc_type, current)
    row = MatrixRow(source=source, doc_type=doc_type, boxes=picked, own=bool(boxes_of(boxes)))
    with store.operation(actor, f"matrix: add {source}"):
        _save_layout(store, [*current, row], actor, f"add {source}")
        read_pdfs(store, actor, only_rows={(source, doc_type)})


def set_boxes(store: RecordStore, source: str, doc_type: str, boxes: str, actor: str) -> None:
    current = current_rows(store)
    if not any((r.source, r.doc_type) == (source, doc_type) for r in current):
        raise ValueError("that row isn't in Across the years")
    picked = MatrixRow(source=source, doc_type=doc_type, boxes=boxes_of(boxes)).boxes
    new = [
        r.model_copy(update={"boxes": picked, "own": bool(picked)})
        if (r.source, r.doc_type) == (source, doc_type)
        else r
        for r in current
    ]
    with store.operation(actor, f"matrix: boxes {source}"):  # the new box read at once
        _save_layout(store, new, actor, f"boxes {source}")
        read_pdfs(store, actor, only_rows={(source, doc_type)})


def remove_row(store: RecordStore, source: str, doc_type: str, actor: str) -> None:
    """Take a row off Across the years (its figures stay, in case it's added back)."""
    current = current_rows(store)
    kept = [r for r in current if (r.source, r.doc_type) != (source, doc_type)]
    if len(kept) == len(current):
        raise ValueError("that row isn't in Across the years")
    _save_layout(store, kept, actor, f"remove {source}")


def set_first_year(store: RecordStore, year: int, actor: str) -> None:
    if not 1990 <= year <= 2100:
        raise ValueError("give a year like 2020")
    _save_layout(store, current_rows(store), actor, f"from {year}", first_year=year)


# The box usually worth tracking for each form (the person's own choices win; see suggest_boxes).
DEFAULT_BOX = {
    "k1_1065": "1",  # ordinary business income; "2" (rental real estate) for real estate funds
    "k1_1120s": "1",
    "k1_1041": "1",
    "1099_composite": "1a",  # ordinary dividends
    "1099_int": "1",
    "1099_div": "1a",  # ordinary dividends
    "1099_misc": "1",
    "1099_nec": "1",
    "1099_r": "1",
    "1099_q": "1",
    "w2": "1",
    "ssa_1099": "3",  # benefits paid
    "1098": "1",  # mortgage interest
    "5498": "1",
    "5498_sa": "2",  # HSA contributions
}
# nothing to put in Across the years
NO_FIGURE = {"account_change_notice", "fbar_filing", "tax_return", "return_paper"}
REAL_ESTATE = re.compile(
    r"propert|apartment|realty|real estate|residential|multifamily|housing|\breit\b|land|homes",
    re.IGNORECASE,
)


def suggest_boxes(
    store: RecordStore, source_id: str, doc_type: str, rows: list[MatrixRow] | None = None
) -> list[str]:
    """The box for a new row: what the person chose for the same type at the same institution,
    else the form's usual box (rental real estate for real estate K-1s)."""
    rows = current_rows(store) if rows is None else rows
    sources = {s.id: s for s in store.list("source")}
    source = sources.get(source_id)
    if source is None:
        return []
    for r in rows:
        other = sources.get(r.source)
        if r.boxes and r.doc_type == doc_type and other and other.institution == source.institution:
            return list(r.boxes)
    from taxbinder.settings import load, usual_box

    box = usual_box(store, doc_type)
    if doc_type == "k1_1065" and doc_type not in load(store).usual_boxes:  # not the person's
        try:
            inst = store.get("institution", source.institution)[0]
        except RecordNotFound:
            inst = None
        names = f"{source.name} {inst.name if inst else ''}"
        if (inst and inst.kind == "real_estate_sponsor") or REAL_ESTATE.search(names):
            box = "2"
    from taxbinder.extract.figures import KEY_BOXES

    return [box] + [b for b in KEY_BOXES.get(doc_type, []) if b != box] if box else []


def key_boxes(store: RecordStore, doc: Any) -> list[str]:  # noqa: ANN401 - a Document
    """A document's key boxes: those its row in Across the years follows (the form's usual
    ones until it's on a row), and those that always matter for its form."""
    from taxbinder.extract.figures import KEY_BOXES
    from taxbinder.settings import usual_box

    c = doc.classification
    if not c or not c.doc_type:
        return []
    exp = next((e for e in store.list("expectation", c.tax_year) if c.tax_year
                and any(x.doc == doc.id for x in e.evidence)), None)  # fmt: skip
    row = exp and next((r for r in current_rows(store)
                        if (r.source, r.doc_type) == (exp.source, exp.doc_type)), None)  # fmt: skip
    if row and row.boxes:
        boxes = list(row.boxes)
    elif exp is not None:
        boxes = suggest_boxes(store, exp.source, exp.doc_type)
    else:
        boxes = [b for b in [usual_box(store, c.doc_type)] if b]
    return list(dict.fromkeys(boxes + KEY_BOXES.get(c.doc_type, [])))


def short_of_key_boxes(store: RecordStore) -> list[tuple[str, str]]:
    """Rows on their form's usual box alone that should follow its key boxes too (rows made
    before W-2s followed tax withheld); never rows the person set."""
    from taxbinder.extract.figures import KEY_BOXES
    from taxbinder.settings import usual_box

    return [
        (r.source, r.doc_type)
        for r in current_rows(store)
        if r.doc_type in KEY_BOXES and not r.own and r.boxes == [usual_box(store, r.doc_type)]
        and len(suggest_boxes(store, r.source, r.doc_type, [])) > 1
    ]


def add_key_boxes(store: RecordStore, actor: str) -> int:
    """Those rows follow their key boxes, read at once; still not the person's own choice."""
    wanted = set(short_of_key_boxes(store))
    if not wanted:
        return 0
    new = [
        r.model_copy(update={"boxes": suggest_boxes(store, r.source, r.doc_type, [])})
        if (r.source, r.doc_type) in wanted
        else r
        for r in current_rows(store)
    ]
    with store.operation(actor, f"matrix: key boxes for {len(wanted)} row(s)"):
        _save_layout(store, new, actor, f"key boxes for {len(wanted)} row(s)")
        read_pdfs(store, actor, only_rows=wanted)
    return len(wanted)


def add_all(store: RecordStore, actor: str) -> int:
    """Add every document the vault expects (any year) that isn't a row yet, each with a
    suggested box. Returns how many were added."""
    current = current_rows(store)
    have = {(r.source, r.doc_type) for r in current}
    new = list(current)
    for source, doc_type, _ in choices(store):
        if (source, doc_type) in have or doc_type in NO_FIGURE:
            continue
        new.append(
            MatrixRow(
                source=source, doc_type=doc_type, boxes=suggest_boxes(store, source, doc_type, new)
            )
        )
        have.add((source, doc_type))
    added = len(new) - len(current)
    if added:
        with store.operation(actor, f"matrix: add {added} row(s)"):  # their documents read at once
            _save_layout(store, new, actor, f"add {added} row(s)")
            read_pdfs(store, actor, only_rows={(r.source, r.doc_type) for r in new[len(current):]})
    return added


def choices(store: RecordStore) -> list[tuple[str, str, str]]:
    """(source id, doc type, label) for every document the vault expects in any year."""
    from taxbinder.doctypes import labels

    names = labels(store)
    sources = {s.id: s for s in store.list("source")}
    senders = {i.id: i.name for i in store.list("institution")}

    def who(s: Any) -> str:  # noqa: ANN401 - a Source
        sender = senders.get(s.institution, s.name)
        return sender if s.name.casefold() == sender.casefold() else f"{sender}, {s.name}"

    pairs = sorted({(e.source, e.doc_type) for e in store.list("expectation")})
    return [(s, t, f"{who(sources[s])} · {names.get(t, t)}") for s, t in pairs if s in sources]


def to_xlsx(store: RecordStore) -> bytes:
    """The grid as an Excel file: whole dollars (sheet "Across the years") and cents ("Exact")."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    cols, data = sorted(years(store), reverse=True), rows(store)  # newest first, as the page
    wb = Workbook()
    for sheet, exact in ((wb.active, False), (wb.create_sheet("Exact"), True)):
        sheet.title = "Exact" if exact else "Across the years"
        sheet.append(["Type", "Sender", "Account", "ID", "Box", *cols])
        for c in sheet[1]:
            c.font = Font(bold=True)
        for r in data:  # a line per box: the row's first, then its others (its sub-rows)
            for i, box in enumerate(r.boxes or [""]):
                head = [r.label, r.sender, r.account, r.ident] if i == 0 else ["", "", "", ""]
                line: list[Any] = [*head, box]
                for y in cols:
                    v = (r.cells[y]._shown() + [None])[i]  # typed, else read
                    line.append(None if v is None else float(v) if exact else int(round(v)))
                sheet.append(line)
        for row in sheet.iter_rows(min_row=2, min_col=6):
            for c in row:
                c.number_format = "#,##0.00;(#,##0.00)" if exact else "#,##0;(#,##0)"
        sheet.column_dimensions["B"].width = 30
        sheet.column_dimensions["C"].width = 24
    _gifts_sheet(store, wb.create_sheet("Donations"), Font(bold=True))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _gifts_sheet(store: RecordStore, sheet: Any, bold: Any) -> None:  # noqa: ANN401
    """Each charity's gifts per year, and the total given (DAF grants not in it)."""
    from taxbinder.donations import by_year

    given, total = by_year(store)
    cols = sorted({*years(store), *total, *(y for g in given for y in g.years)}, reverse=True)
    sheet.append(["Charity", "", *cols])
    for g in given:
        daf = "from your DAF" if g.from_daf else ""
        sheet.append([g.name, daf, *(g.years.get(y) for y in cols)])
    sheet.append(["Total given", "", *(total.get(y) for y in cols)])
    for c in (*sheet[1], *sheet[sheet.max_row]):
        c.font = bold
    for row in sheet.iter_rows(min_row=2, min_col=3):
        for c in row:
            c.number_format = "#,##0.00"
    sheet.column_dimensions["A"].width = 34


def _document(store: RecordStore, exp: Any) -> str | None:  # noqa: ANN401
    """The year's document for a checklist row: the newest one that isn't superseded."""
    links = [x for x in exp.evidence if x.role not in ("superseded", "bill", "other")]
    return max(links, key=lambda x: x.linked_at).doc if links else None


@perf.timed("matrix.read_pdfs")
def read_pdfs(
    store: RecordStore,
    actor: str,
    only_years: set[int] | None = None,
    only_docs: set[str] | None = None,
    only_rows: set[tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Read every row's boxes from each year's document; keep what's new or different as
    suggestions (never changing a value). Returns counts: read, suggested, conflicts."""
    from taxbinder import modelread
    from taxbinder.extract.figures import confirmed, read_boxes
    from taxbinder.ingest.inbox import scanned_pages

    counts = {"read": 0, "suggested": 0, "conflicts": 0}
    data = rows(store)
    exps = {(e.year, e.source, e.doc_type): e for e in store.list("expectation")}  # once
    for year in years(store):
        if only_years is not None and year not in only_years:
            continue
        fs, rev = _figures(store, year)
        dismissed = {(d.source, d.doc_type, d.doc) for d in fs.dismissed}
        suggested, done = [], set()
        for r in data:
            pair = (r.source.id, r.doc_type)
            if not r.boxes or (only_rows is not None and pair not in only_rows):
                continue
            if only_rows is not None:
                done.add((r.source.id, r.doc_type))  # its box changed: older readings go
            exp = exps.get((year, r.source.id, r.doc_type))
            doc_id = _document(store, exp) if exp is not None else None
            if doc_id is None or (r.source.id, r.doc_type, doc_id) in dismissed:
                continue
            if only_docs is not None and doc_id not in only_docs:
                continue
            done.add((r.source.id, r.doc_type))
            doc, _ = store.get("document", doc_id)
            scan = scanned_pages(store, doc)  # read from the words recognised on a scan
            if doc.mime != "application/pdf" and not scan:
                continue
            model = modelread.reading(store, doc) or None  # compared with the rules
            got = read_boxes(store.blobs.get(doc.sha256), r.doc_type, r.boxes, scan, year, model)
            got = confirmed(got, doc.confirmed_boxes, r.boxes)  # the person's answers win
            if not got:
                continue
            counts["read"] += 1
            values = [got[b].value if b in got else None for b in r.boxes]
            cell = Cell(r.cells[year].values, values)
            if cell.pending or cell.conflict:
                counts["conflicts" if cell.conflict else "suggested"] += 1
                pages = [got[b].page if b in got else None for b in r.boxes]
                suggested.append(
                    Suggestion(
                        source=r.source.id,
                        doc_type=r.doc_type,
                        doc=doc_id,
                        values=values,
                        pages=pages,
                        boxes=r.boxes,
                        scanned=bool(scan),
                        checks=sorted({got[b].check for b in r.boxes if b in got and got[b].check}),
                    )
                )
        kept = [x for x in fs.suggested if (x.source, x.doc_type) not in done]
        if only_docs is not None or only_rows is not None:
            suggested = kept + suggested  # other rows' suggestions stay
        if suggested != fs.suggested:
            with store.operation(actor, f"matrix: read figures {year}"):
                store.put(fs.model_copy(update={"suggested": suggested}), rev)
    return counts


def accept(store: RecordStore, source: str, doc_type: str, year: int, actor: str) -> Cell:
    """Take the PDF's values: every box it read."""
    row = next((r for r in rows(store) if (r.source.id, r.doc_type) == (source, doc_type)), None)
    if row is None or year not in row.cells or not row.cells[year].suggested:
        raise ValueError("nothing to accept there")
    cell = row.cells[year]
    values = [
        s if s is not None else v
        for v, s in zip(cell.values, cell.suggested or [], strict=False)
    ]
    text = SEP.join("" if v is None else str(v) for v in values)
    result = set_cell(store, source, doc_type, year, text, actor)
    _drop_suggestion(store, year, source, doc_type, actor, dismiss=False)
    return result


def dismiss(store: RecordStore, source: str, doc_type: str, year: int, actor: str) -> None:
    _drop_suggestion(store, year, source, doc_type, actor, dismiss=True)


def _drop_suggestion(
    store: RecordStore, year: int, source: str, doc_type: str, actor: str, dismiss: bool
) -> None:
    fs, rev = _figures(store, year)
    sug = next((x for x in fs.suggested if (x.source, x.doc_type) == (source, doc_type)), None)
    if sug is None:
        return
    update: dict[str, Any] = {"suggested": [x for x in fs.suggested if x is not sug]}
    if dismiss:
        update["dismissed"] = [
            *fs.dismissed,
            Dismissed(source=source, doc_type=doc_type, doc=sug.doc),
        ]
    verb = "dismiss" if dismiss else "accepted"
    with store.operation(actor, f"matrix: {verb} {year} {source}"):
        store.put(fs.model_copy(update=update), rev)
