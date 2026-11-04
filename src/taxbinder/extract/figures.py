"""Box values read from a tax form by label and position (deterministic, on the computer).

The forms the main-page reader knows (`forms.py`: K-1 1065, 1099-DIV, -INT, -R, 1098, W-2,
SSA-1099, 5498-SA, and a consolidated 1099's parts) are read by it first. A box it didn't find,
and the other forms, are read here: forms flattened (form-field values become page text), each
box found by its printed caption ("Net rental real estate income (loss)"), its area from the
caption to the next caption right and below; the number inside that area is the box's value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from taxbinder.pdfdoc import Box, Page, Pdf

# Labels as printed on the IRS forms (the start of each box's caption), per document type.
K1_1065 = {
    "1": "Ordinary business income",
    "2": "Net rental real estate income",
    "3": "Other net rental income",
    "4a": "Guaranteed payments for services",
    "4b": "Guaranteed payments for capital",
    "4c": "Total guaranteed payments",
    "5": "Interest income",
    "6a": "Ordinary dividends",
    "6b": "Qualified dividends",
    "6c": "Dividend equivalents",
    "7": "Royalties",
    "8": "Net short-term capital gain",
    "9a": "Net long-term capital gain",
    "9b": "Collectibles (28%) gain",
    "9c": "Unrecaptured section 1250 gain",
    "10": "Net section 1231 gain",
    "11": "Other income",
    "12": "Section 179 deduction",
    "14": "Self-employment earnings",
    "19": "Distributions",
    "21": "Foreign taxes paid",
}
K1_1120S = {
    "1": "Ordinary business income",
    "2": "Net rental real estate income",
    "3": "Other net rental income",
    "4": "Interest income",
    "5a": "Ordinary dividends",
    "5b": "Qualified dividends",
    "6": "Royalties",
    "7": "Net short-term capital gain",
    "8a": "Net long-term capital gain",
    "9": "Net section 1231 gain",
    "11": "Section 179 deduction",
    "16": "Items affecting shareholder basis",
}
K1_1041 = {
    "1": "Interest income",
    "2a": "Ordinary dividends",
    "2b": "Qualified dividends",
    "3": "Net short-term capital gain",
    "4a": "Net long-term capital gain",
    "6": "Ordinary business income",
    "7": "Net rental real estate income",
    "8": "Other rental income",
}
LABELS: dict[str, dict[str, str]] = {
    "k1_1065": K1_1065,
    "k1_1120s": K1_1120S,
    "k1_1041": K1_1041,
    "1099_composite": {
        "1a": "Total ordinary dividends",
        "1b": "Qualified dividends",
        "2a": "Total capital gain distr",
        "1": "Interest income",
        "3": "Interest on U.S. Savings Bonds",
        "8": "Tax-exempt interest",
        "gain": "Net gain or loss (1099-B)",
    },
    "1099_int": {
        "1": "Interest income",
        "3": "Interest on U.S. Savings Bonds",
        "8": "Tax-exempt interest",
    },
    "1099_div": {
        "1a": "Total ordinary dividends",
        "1b": "Qualified dividends",
        "2a": "Total capital gain distr",
        "7": "Foreign tax paid",
    },
    "1099_r": {"1": "Gross distribution", "2a": "Taxable amount"},
    "1099_misc": {"1": "Rents", "2": "Royalties", "3": "Other income"},
    "1099_nec": {"1": "Nonemployee compensation"},
    "1098": {
        "1": "Mortgage interest received",
        "2": "Outstanding mortgage principal",
        "10": "Real estate taxes",  # paid from escrow (no property tax question then)
    },
    "ssa_1099": {"3": "Benefits paid in", "5": "Net benefits for"},
    "w2": {
        "1": "Wages, tips, other compensation",
        "2": "Federal income tax withheld",
        "12a": "See instructions for box 12",  # the amount; its code letter is skipped
        "16": "State wages, tips, etc.",  # the first state's line
        "17": "State income tax",
    },
    "5498": {"1": "IRA contributions"},
    "5498_sa": {"2": "Total contributions made in"},
}

# Key boxes: beside a row's own boxes, those that always matter for a
# form. Its row in Across the years follows all of them; a doubt about one is asked, never filed.
KEY_BOXES: dict[str, list[str]] = {
    "w2": ["1", "2", "16", "17"],
    "1099_composite": ["1a", "1", "2a", "gain"],  # dividends, interest, cap. gain distr., 1099-B
}

# Captions of boxes not read (coded boxes, other parts of the form): they only bound the others.
BOUNDS = {
    "k1_1065": [
        "Credits",
        "Schedule K-3 is attached",
        "Alternative minimum tax",
        "Tax-exempt income",
        "Other deductions",
        "Other information",
        "Foreign taxes",
        "Part II",
        "Part I ",
    ],
    "k1_1120s": ["Credits", "Other deductions", "Other information", "Alternative minimum tax"],
    "k1_1041": ["Directly apportioned deductions", "Final year deductions", "Other information"],
}
NUMBER = re.compile(r"^\(?-?\$?\(?-?\d[\d,]*(?:\.\d*)?\)?-?$")
BELOW = 60  # how far below its label a value may sit when nothing else bounds the box (points)


def amount(token: str) -> Decimal | None:
    """'-12,345.' → -12345; '0.' → 0; '(12,345)' → -12345; '$1,234.50' → 1234.50."""
    t = token.strip()
    negative = t.startswith(("-", "(")) or t.endswith((")", "-")) or "(-" in t
    digits = re.sub(r"[^\d.]", "", t).rstrip(".")
    if not digits or digits.count(".") > 1:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


@dataclass(frozen=True)
class Read:
    box: str
    value: Decimal
    page: int  # 1-based
    check: str = ""  # a check of the form's arithmetic this value fails ("4 = 6.2% of 3")


# Document types → the form the main-page reader knows; a consolidated 1099 is its parts
FORM_OF = {
    "k1_1065": "K1_1065", "1099_div": "1099_DIV", "1099_int": "1099_INT", "1099_r": "1099_R",
    "1098": "1098", "w2": "W2", "ssa_1099": "SSA_1099", "5498_sa": "5498_SA",
}  # fmt: skip
COMPOSITE_INT = {"1", "3", "8"}  # a consolidated 1099's interest boxes; the others are dividends
B_GAIN = "gain"  # a consolidated 1099's 1099-B: the gain column of its summary rows, added up


def _money(value: object) -> Decimal | None:
    """A read box as one amount: a number, or a coded box's lines added up (K-1 19, W-2 12a)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return Decimal(str(round(value, 2)))
    if isinstance(value, list):
        amounts = [
            i["amount"] for i in value if isinstance(i, dict) and i.get("amount") is not None
        ]
        return Decimal(str(round(sum(amounts), 2))) if amounts else None
    return None  # a date, a code, ticked boxes: not an amount


def _whole(value: Decimal | None) -> str:
    return "nothing" if value is None else f"{value:,.0f}"


def _doubt(b: dict, value: Decimal | None) -> str:
    """Why a box the model read needs a look, or "" when the two readings agree."""
    if b["status"] in ("confirmed", "rules-only"):
        return ""
    reasons = []
    if b["status"] == "check-failed":
        reasons.append(b.get("check", ""))
    if "rule_value" in b:  # they differ (a failed check can come on top)
        rules = _whole(_money(b["rule_value"]))
        reasons.append(f"the rules read {rules}, the AI model {_whole(value)}")
    elif b["status"] == "llm":
        reasons.append("only the AI model found it")
    if not b.get("grounded", True):
        reasons.append("the AI model's value isn't printed on the page")
    return "; ".join(r for r in reasons if r)


def _from_forms(
    data: bytes,
    doc_type: str,
    boxes: list[str],
    pages: list[Page] | None,
    year: int | None,
    model: dict | None = None,
) -> tuple[dict[str, Read], set[str]]:
    """What the main-page reader found, or the cross-check when the model has read the document:
    (values, the boxes found empty)."""
    from taxbinder.extract import forms

    checked = (model or {}).get("forms", {})
    read = checked if any(n in checked for n in _names(doc_type)) else forms.read(data, pages, year)
    got: dict[str, Read] = {}
    empty: set[str] = set()
    for box in boxes:
        if doc_type == "1099_composite" and box == B_GAIN:
            found = read.get("1099_B", {}).get("boxes", {})
            rows = [b for k, b in found.items() if k.endswith("_gain")]
        else:
            if doc_type == "1099_composite":
                name = "1099_INT" if box in COMPOSITE_INT else "1099_DIV"
            else:
                name = FORM_OF[doc_type]
            rows = [read.get(name, {}).get("boxes", {}).get(box)]
        taken = [_taken(b) for b in rows if b is not None and b["status"] != "missing"]
        values = [v for v, _, _, _ in taken if v is not None]
        if values:
            why = "; ".join(dict.fromkeys(w for _, w, _, _ in taken if w))
            page = next(p for v, _, p, _ in taken if v is not None)
            got[box] = Read(box, Decimal(sum(values)), page, why)
        elif taken and all(blank for *_, blank in taken):
            empty.add(box)  # its label is there and nothing in it: blank, not zero
    return got, empty


def _taken(b: dict) -> tuple[Decimal | None, str, int, bool]:
    """One read box: its value (the rules' where the model differs: right far more often on the
    forms measured; the model's is in the why), why it needs a look, its page, and whether it's
    blank."""
    value = _money(b["value"])
    if "grounded" in b:  # the cross-check
        if "rule_value" in b and _money(b["rule_value"]) is not None:
            value = _money(b["rule_value"])
        doubt = _doubt(b, _money(b["value"]))
        return value, doubt, b["page"], value is None and not doubt
    blank = b["status"] == "blank" or (value is None and b["value"] in (None, []))
    return value, b.get("check", ""), b["page"], blank


def _names(doc_type: str) -> list[str]:
    return ["1099_DIV", "1099_INT"] if doc_type == "1099_composite" else [FORM_OF.get(doc_type, "")]


def _labels(page: Page, labels: dict[str, str]) -> dict[str, Box]:
    found = {}
    for box, caption in labels.items():
        hits = page.search(caption)
        if hits:
            found[box] = hits[0]
    return found


def _area(box: str, rect: Box, all_labels: dict[str, Box], page: Page) -> Box:
    """From the label (and its box number to the left) to the next label right and below."""
    right, bottom = page.width, rect.y1 + BELOW
    for other, r in all_labels.items():
        if other == box:
            continue
        same_row = r.y0 < rect.y1 + 4 and r.y1 > rect.y0 - 4
        if same_row and r.x0 > rect.x1:
            right = min(right, r.x0 - 10)  # the other box's number sits just left of its caption
        overlaps = r.x0 < rect.x1 + 40 and r.x1 > rect.x0 - 40
        if overlaps and r.y0 > rect.y1 - 1:
            bottom = min(bottom, r.y0 - 1)
    return Box(rect.x0 - 24, rect.y0 - 2, right, bottom)


def read_boxes(
    data: bytes,
    doc_type: str,
    boxes: list[str],
    pages: list[Page] | None = None,
    year: int | None = None,
    model: dict | None = None,
) -> dict[str, Read]:
    """The values of `boxes` on this document (only those found). `pages`: already read, e.g.
    recognised from a scan, instead of the PDF's own text; `year`: the tax year (for checks);
    `model`: what the model read on it (`crosscheck.read`), compared with the rules."""
    got: dict[str, Read] = {}
    empty: set[str] = set()
    if doc_type in FORM_OF or doc_type == "1099_composite":
        got, empty = _from_forms(data, doc_type, boxes, pages, year, model)
    rest = [b for b in boxes if b not in got and b not in empty and b != B_GAIN]
    return got | (_by_caption(data, doc_type, rest, pages) if rest else {})


def confirmed(
    got: dict[str, Read], answers: dict[str, Decimal], boxes: list[str]
) -> dict[str, Read]:
    """The readings with the amounts the person confirmed (`Document.confirmed_boxes`)
    in their place: nothing left to check in those boxes."""
    out = dict(got)
    for box, value in answers.items():
        if box in boxes:
            out[box] = Read(box, value, got[box].page if box in got else 1, "")
    return out


def _by_caption(
    data: bytes, doc_type: str, boxes: list[str], pages: list[Page] | None = None
) -> dict[str, Read]:
    labels = LABELS.get(doc_type, {})
    wanted = {b: labels[b] for b in boxes if b in labels}
    if not wanted:
        return {}
    if pages:
        pdf = _Pages(pages)
    else:
        try:
            pdf = Pdf(data, flatten=True)  # form-field values become page text
        except ValueError:
            return {}  # password-protected
    captions = [" ".join(c.lower().split()) for c in labels.values()]
    out: dict[str, Read] = {}
    try:
        for index in range(len(pdf)):
            if len(out) == len(wanted):
                break  # everything found (values are almost always on the form's first page)
            text = " ".join(pdf.text(index).lower().split())
            if not any(c in text for c in captions):
                continue  # footnotes, statements, state schedules: not the form
            page = pdf.page(index)
            rects = _labels(page, labels)  # every known label on the page bounds the others
            if not rects:
                continue
            bounds = dict(rects)
            for i, caption in enumerate(BOUNDS.get(doc_type, [])):
                bounds |= {f"_b{i}_{j}": r for j, r in enumerate(page.search(caption))}
            for box in wanted:
                if box in out or box not in rects:
                    continue
                area, caption = _area(box, rects[box], bounds, page), rects[box]
                candidates = []
                for word in page.words:
                    w, b = word.text, word.box
                    if not NUMBER.match(w) or not area.contains_point(*b.centre):
                        continue
                    if b.intersects(caption) or (
                        b.x1 <= caption.x0 + 1 and abs(b.y0 - caption.y0) < 4
                    ):
                        continue  # part of the caption, or the box's own number ("2")
                    if re.fullmatch(r"\d{1,2}[a-z]?", w):
                        continue  # bare "12", "6a": a box number (values print as "-4,987.")
                    if re.fullmatch(r"(?:19|20)\d{2}", w) and abs(b.y0 - caption.y0) < 4:
                        continue  # the year in a caption ("Benefits paid in 2025")
                    if (v := amount(w)) is not None:
                        candidates.append((b.y0, -b.x1, v))
                if candidates:
                    out[box] = Read(box, sorted(candidates)[0][2], page.number)
    finally:
        pdf.close()
    return out


class _Pages:
    """Pages already read (from a scan), looking like an opened `Pdf` to `read_boxes`."""

    def __init__(self, pages: list[Page]) -> None:
        self._pages = pages

    def __len__(self) -> int:
        return len(self._pages)

    def text(self, index: int) -> str:
        return self._pages[index].text

    def page(self, index: int) -> Page:
        return self._pages[index]

    def close(self) -> None:
        pass


def _mask(text: str) -> str:
    return re.sub(r"\d", "9", text)


def masked_layout(data: bytes) -> str:
    """A document's layout with every digit replaced by 9: labels, positions, form fields.
    Safe to share for tuning the readers (no names are removed, so check before sharing)."""
    pdf = Pdf(data)
    fields = pdf.fields()
    pdf.close()
    lines = [f"form fields: {len(fields)}"]
    lines += [f"  {name} = {_mask(value)}" for name, value in fields[:200]]
    pdf = Pdf(data, flatten=True)
    for page in pdf.pages():
        lines.append(f"--- page {page.number} ({page.width:.0f} x {page.height:.0f}) ---")
        for box, text in page.lines():
            lines.append(f"x={box.x0:5.0f}-{box.x1:5.0f} y={box.y0:5.0f}  {_mask(text)}")
    pdf.close()
    return "\n".join(lines)
