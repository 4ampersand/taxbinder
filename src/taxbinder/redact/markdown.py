"""A document as redacted Markdown for an LLM (§9.1), with page markers and a leak check.

Forms flattened first, the
page text read line by line, tables kept as Markdown tables, every value replaced everywhere,
and the result re-checked. Unlike redact-pdf, a leftover known value is an error (nothing is
returned), and scanned pages are left out (they can't be checked for personal details yet).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from taxbinder.pdfdoc import Box, Pdf
from taxbinder.redact.detect import Finder, apply, find


class Leak(Exception):
    """A value that must be hidden is still in the output."""


@dataclass
class Redacted:
    markdown: str
    counts: Counter[str] = field(default_factory=Counter)  # placeholder kind → how many hidden
    scanned_pages: list[int] = field(default_factory=list)  # left out: no text to check
    pages: int = 0


def _cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def _table(rows: list[list[str | None]]) -> str:
    rows = [[_cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def page_markdown(pdf: Pdf, index: int) -> tuple[str, bool]:
    """(Markdown of the page, whether it looks scanned): text lines grouped into paragraphs in
    reading order, with ruled tables as Markdown tables."""
    page = pdf.page(index)
    if not page.words:
        return "", page.images > 0
    tables: list[tuple[Box, list[list[str | None]]]] = []
    if page.paths >= 4:  # ruled lines; plain statement pages have none (and are slow to scan)
        try:
            tables = pdf.tables(index)
        except Exception:  # table detection is a nicety, never a reason to fail
            tables = []
    items: list[tuple[float, float, str]] = []
    for box, rows in tables:
        if md := _table(rows):
            items.append((box.y0, box.x0, md))
    in_tables = {
        w for _, rows in tables for row in rows for cell in row if cell for w in cell.split()
    }
    for box, lines in page.blocks():  # a line the tables missed is kept: no text is lost
        kept = [
            text
            for text in lines
            if not (
                any(t.overlap_area(box) > 0.5 * max(box.area, 1) for t, _ in tables)
                and all(w in in_tables for w in text.split())
            )
        ]
        if kept:
            items.append((box.y0, box.x0, "\n".join(kept)))
    return "\n\n".join(md for _, _, md in sorted(items)), False


def redact_pdf_to_markdown(data: bytes, finders: list[Finder], tokens) -> Redacted:  # noqa: ANN001
    pdf = Pdf(data, flatten=True)  # form-field values and comments become text, so they're checked
    out = Redacted(markdown="", pages=len(pdf))
    parts = []
    found: dict[str, str] = {}  # every value detected anywhere → its placeholder
    try:
        for index in range(len(pdf)):
            md, scanned = page_markdown(pdf, index)
            if not md.strip():
                if scanned:
                    out.scanned_pages.append(index + 1)
                continue
            raw = pdf.text(index)  # as printed: an address's parts sit together here
            spans = find(md, finders, tokens)
            for text, hits in ((raw, find(raw, finders, tokens)), (md, spans)):
                for sp in hits:
                    found.setdefault(" ".join(text[sp.start : sp.end].split()), sp.token)
            parts.append(f"**— Page {index + 1} —**\n\n{apply(md, spans)}")
    finally:
        pdf.close()
    # Sweep: every value found on any page (in the raw text or the Markdown) is replaced wherever
    # it appears, so how the layout was reconstructed never decides what stays hidden.
    text = "\n\n".join(parts) + "\n"
    for value, token in sorted(found.items(), key=lambda vt: -len(vt[0])):
        if len(value) < 3:
            continue
        pattern = r"\s+".join(re.escape(w) for w in value.split())
        text = re.sub(rf"(?<![\w\[]){pattern}(?![\w\]])", f"[{token}]", text, flags=re.IGNORECASE)
    out.counts.update(t.rsplit("_", 1)[0] for t in re.findall(r"\[([A-Z]+_\d+)\]", text))
    out.markdown = text
    check(out.markdown, finders)
    return out


def check(text: str, finders: list[Finder]) -> None:
    """Nothing the vault knows, and nothing a pattern would hide, may be left."""
    for f in finders:
        if m := f.regex.search(text):
            start, end = m.span(1) if m.re.groups else m.span()
            if end - start >= 2:
                raise Leak(f"a {f.prefix} value is still in the output; nothing was produced")
