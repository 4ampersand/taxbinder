"""PDF pages as words with positions, for reading figures and redacting (pypdfium2, Apache/BSD).

Coordinates are in points from the page's top-left corner, like most layout code expects.
Fillable forms can be flattened first, so form-field values become ordinary page text.
"""

from __future__ import annotations

import io
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

# PDFium may be used by one thread at a time (two at once corrupt its memory and crash the app):
# every use of it, here and elsewhere, holds this lock.
PDFIUM = threading.RLock()


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def union(self, other: Box) -> Box:
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def intersects(self, other: Box) -> bool:
        return (
            self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1
        )

    def overlap_area(self, other: Box) -> float:
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        return w * h if w > 0 and h > 0 else 0.0

    @property
    def area(self) -> float:
        return max(self.x1 - self.x0, 0) * max(self.y1 - self.y0, 0)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Word:
    text: str
    box: Box
    line: int  # words with the same number are on one text line


@dataclass
class Page:
    number: int  # 1-based
    width: float
    height: float
    text: str  # the page's text, lines separated by newlines
    words: list[Word] = field(default_factory=list)
    paths: int = 0  # drawn path objects (ruled lines, boxes)
    images: int = 0  # image objects (a page with images and no text is a scan)

    def search(self, phrase: str) -> list[Box]:
        """Where a phrase is printed (case and spacing ignored): one box per occurrence."""
        joined, spans = self._joined
        target = " ".join(phrase.lower().split())
        out = []
        for m in re.finditer(re.escape(target), joined):
            hit = [w.box for (s, e, w) in spans if s < m.end() and m.start() < e]
            if hit:
                box = hit[0]
                for b in hit[1:]:
                    box = box.union(b)
                out.append(box)
        return out

    @cached_property
    def _joined(self) -> tuple[str, list[tuple[int, int, Word]]]:
        parts, spans, pos = [], [], 0
        for w in self.words:
            t = w.text.lower()
            spans.append((pos, pos + len(t), w))
            parts.append(t)
            pos += len(t) + 1
        return " ".join(parts), spans

    def lines(self) -> list[tuple[Box, str]]:
        """Each text line's box and text, top to bottom. A line is split where the gap between
        two words is wide (columns: pdfium puts words at the same height on one line)."""
        groups: dict[int, list[Word]] = {}
        for w in self.words:
            groups.setdefault(w.line, []).append(w)
        out = []
        for ws in groups.values():
            ws.sort(key=lambda w: w.box.x0)
            segment = [ws[0]]
            for prev, w in zip(ws, ws[1:], strict=False):
                height = max(prev.box.y1 - prev.box.y0, 1)
                if w.box.x0 - prev.box.x1 > max(2.5 * height, 12):  # another column
                    out.append(_line(segment))
                    segment = []
                segment.append(w)
            out.append(_line(segment))
        return sorted(out, key=lambda lt: (round(lt[0].y0), lt[0].x0))

    def blocks(self) -> list[tuple[Box, list[str]]]:
        """Paragraphs: lines close below each other and overlapping sideways (one column)."""
        open_: list[tuple[Box, list[str], Box]] = []  # (block box, lines, last line box)
        for box, text in self.lines():
            height = max(box.y1 - box.y0, 1)
            for i, (bbox, texts, last) in enumerate(open_):
                below = 0 <= box.y0 - last.y1 <= 0.8 * height or abs(box.y0 - last.y0) < 1
                beside = box.x0 < last.x1 and last.x0 < box.x1
                if below and beside:
                    open_[i] = (bbox.union(box), [*texts, text], box)
                    break
            else:
                open_.append((box, [text], box))
        return sorted(((b, t) for b, t, _ in open_), key=lambda bt: (round(bt[0].y0), bt[0].x0))


def _line(words: list[Word]) -> tuple[Box, str]:
    box = words[0].box
    for w in words[1:]:
        box = box.union(w.box)
    return box, " ".join(w.text for w in words)


def _words(textpage: pdfium.PdfTextPage, height: float) -> tuple[str, list[Word]]:
    n = textpage.count_chars()
    text = textpage.get_text_range(0, n) if n else ""
    words: list[Word] = []
    current, box, line = [], None, 0

    def flush() -> None:
        nonlocal current, box
        if current and box is not None:
            words.append(Word("".join(current), box, line))
        current, box = [], None

    for i, ch in enumerate(text[:n]):
        if ch in "\r\n":
            flush()
            if ch == "\n":
                line += 1
            continue
        if ch.isspace():
            flush()
            continue
        left, bottom, right, top = textpage.get_charbox(i, loose=True)  # font height, not glyph
        b = Box(left, height - top, right, height - bottom)
        box = b if box is None else box.union(b)
        current.append(ch)
    flush()
    return text, words


class Pdf:
    """An opened PDF; pages are read on demand. Raises ValueError for a password-protected file."""

    def __init__(self, data: bytes, flatten: bool = False) -> None:
        self.data = data
        self._plumber = None
        with PDFIUM:
            try:
                self._doc = pdfium.PdfDocument(data)
            except pdfium.PdfiumError as e:
                raise ValueError("the PDF is password-protected or unreadable") from e
            if flatten:
                self._doc.init_forms()  # before any page is opened (pdfium requires it)
                if self.has_forms():
                    self._flatten()

    def has_forms(self) -> bool:
        with PDFIUM:
            if self._doc.get_formtype() != pdfium_c.FORMTYPE_NONE:
                return True
            for i in range(len(self._doc)):
                page = self._doc[i]
                try:
                    if pdfium_c.FPDFPage_GetAnnotCount(page.raw) > 0:
                        return True
                finally:
                    page.close()
            return False

    def _flatten(self) -> None:
        """Form-field values and annotations become page content (like printing the form)."""
        for i in range(len(self._doc)):
            page = self._doc[i]
            # straight to pdfium: the wrapper refuses annotations on a PDF without a form
            pdfium_c.FPDFPage_Flatten(page.raw, pdfium_c.FLAT_NORMALDISPLAY)
            page.close()
        buf = io.BytesIO()
        self._doc.save(buf)
        self._doc.close()
        self.data = buf.getvalue()
        self._doc = pdfium.PdfDocument(self.data)

    def __len__(self) -> int:
        with PDFIUM:
            return len(self._doc)

    def text(self, index: int) -> str:
        """Just the page's text (fast): to decide whether the page is worth reading closely."""
        with PDFIUM:
            page = self._doc[index]
            try:
                tp = page.get_textpage()
                return tp.get_text_range(0, tp.count_chars()) if tp.count_chars() else ""
            finally:
                page.close()

    def page(self, index: int) -> Page:
        with PDFIUM:
            page = self._doc[index]
            try:
                width, height = page.get_size()
                text, words = _words(page.get_textpage(), height)

                def count(kind: int) -> int:
                    return sum(1 for _ in page.get_objects(filter=[kind], max_depth=2))

                paths = count(pdfium_c.FPDF_PAGEOBJ_PATH)
                return Page(index + 1, width, height, text, words, paths,
                            count(pdfium_c.FPDF_PAGEOBJ_IMAGE))  # fmt: skip
            finally:
                page.close()

    def pages(self) -> Iterator[Page]:
        for i in range(len(self._doc)):
            yield self.page(i)

    def tables(self, index: int) -> list[tuple[Box, list[list[str | None]]]]:
        """Ruled tables on a page (pdfplumber, MIT): each table's box and its rows."""
        if self._plumber is None:
            import pdfplumber

            self._plumber = pdfplumber.open(io.BytesIO(self.data))  # once per document
        page = self._plumber.pages[index]
        try:
            return [(Box(*t.bbox), t.extract()) for t in page.find_tables()]
        finally:
            page.close()  # pdfplumber keeps parsed pages otherwise

    def fields(self) -> list[tuple[str, str]]:
        """Form fields (name, value), as the file stores them."""
        import pdfplumber

        out = []
        with pdfplumber.open(io.BytesIO(self.data)) as pdf:
            for annot in pdf.annots:
                data = annot.get("data") or {}
                name, value = data.get("T"), data.get("V")
                if name is None:
                    continue
                as_text = lambda v: v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)  # noqa: E731
                out.append((as_text(name), "" if value is None else as_text(value).strip("/")))
        return out

    def close(self) -> None:
        if self._plumber is not None:
            self._plumber.close()
        with PDFIUM:
            self._doc.close()
