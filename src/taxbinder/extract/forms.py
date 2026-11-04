"""The main page of each tax form in a document, and its boxes read by label and place.

Per page: words with positions
from the text layer (pdfplumber), or the words recognised on a scan. Each page is scored for the
known forms (title or OMB number, plus box labels): a form's **main page** has its OMB number and
enough of its labels, and isn't an instruction page. On a main page each box's label is found
(exactly, or nearly for a misread scan) and its value taken from the same line (statement style:
"label ..... 1,234.56") or from the box's area below the label (IRS form style). Coded boxes
(W-2 12, K-1 11–20) give their lines as code and amount, checkboxes the names of those ticked,
a 1099-B its summary table by Form 8949 box letter ("D_gain"). Then the forms' own arithmetic is
checked (W-2 box 4 = 6.2% of box 3, 1b ≤ 1a, …).

Box status: ok | blank (label found, nothing in it) | missing (label not found) | incomplete (a
coded line without its code) | check-failed (breaks one of the form's checks). Values are never
logged.
"""

from __future__ import annotations

import difflib
import io
import re
from collections.abc import Callable
from typing import Any

from taxbinder import perf
from taxbinder.pdfdoc import Page, Pdf

Word = dict[str, Any]  # {"text", "x0", "x1", "top", "bottom"}: points from the page's top left
Seg = dict[str, Any]  # words of one form box on one line: {"words", "line", "text", x0 … bottom}

MTG = r"m[o0]r\w{0,2}g\w{0,2}g\w"  # "mortgage", also as a scan misreads it ("morlgage")
# Social Security wage base per year (SSA), for the W-2 check 3 + 7 ≤ base; other years: no check
WAGE_BASE = {2024: 168600, 2025: 176100, 2026: 184500}


def _w2_base(v: dict[str, Any], year: int | None) -> bool:
    return year not in WAGE_BASE or v["3"] + v["7"] <= WAGE_BASE[year]


# --- the forms ----------------------------------------------------------------------------------
# Each box: id -> label pattern (matched at the start of a segment's text, lower case). Coded boxes
# hold lines of (code, amount). Checks: (description, boxes, test on the values; missing = 0).
FORMS: dict[str, dict[str, Any]] = {
    "K1_1065": {
        "title": r"schedule k-1", "omb": r"1545-0123",
        "boxes": {
            "1": "ordinary business income", "2": "net rental real estate income",
            "3": "other net rental income", "4a": "guaranteed payments for services",
            "4b": "guaranteed payments for capital", "4c": "total guaranteed payments",
            "5": "interest income", "6a": "ordinary dividends", "6b": "qualified dividends",
            "6c": "dividend equivalents", "7": "royalties", "8": "net short-term capital gain",
            "9a": "net long-term capital gain", "9b": r"collectibles \(28%\) gain",
            "9c": "unrecaptured section 1250 gain", "10": "net section 1231 gain",
            "11": r"other income \(loss\)", "12": "section 179 deduction",
            "13": "other deductions", "14": "self-employment earnings", "15": "credits",
            "17": r"alternative minimum tax", "18": "tax-exempt income and",
            "19": "distributions", "20": "other information", "21": "foreign taxes paid or accrued",
        },
        "coded": {"11", "13", "14", "15", "17", "18", "19", "20"},
        "other_ids": {"16", "22", "23"},  # printed on the form, not read
        "checks": [
            ("6b <= 6a", ("6b", "6a"), lambda v, y: v["6b"] <= v["6a"]),
            ("4c = 4a + 4b", ("4c", "4a", "4b"), lambda v, y: abs(v["4c"] - v["4a"] - v["4b"]) < 1),
        ],
    },
    "1099_DIV": {
        "title": r"1099-div", "omb": r"1545-0110",
        "boxes": {
            "1a": "total ordinary dividends", "1b": "qualified dividends",
            "2a": "total capital gain distr", "2b": "unrecap", "2c": "section 1202 gain",
            "2d": r"collectibles \(28%\) gain", "2e": "section 897 ordinary",
            "2f": "section 897 capital", "3": "non-?dividend distributions",
            "4": "federal income tax withheld", "5": "section 199a dividends",
            "6": "investment expenses", "7": "foreign tax paid", "9": "cash liquidation",
            "10": "non-?cash liquidation", "12": "exempt-interest dividends",
            "13": "specified private activity",
        },
        "coded": set(),
        "other_ids": {"8", "11", "14", "15", "16"},
        "checks": [
            (f"{b} <= {t}", (b, t), lambda v, y, b=b, t=t: v[b] <= v[t])
            for b, t in [("1b", "1a"), ("5", "1a"), ("2e", "1a"), ("2b", "2a"), ("2c", "2a"),
                         ("2d", "2a"), ("2f", "2a")]
        ],
    },
    "1099_INT": {
        "title": r"1099-int", "omb": r"1545-0112",
        "boxes": {
            "1": "interest income", "2": "early withdrawal penalty",
            "3": r"interest on u\.?s\.?( savings bonds|$)", "4": "federal income tax withheld",
            "5": "investment expenses", "6": "foreign tax paid", "8": "tax-exempt interest",
            "9": "specified private activity bond interest", "10": "market discount",
            "11": r"bond premium(?! on)", "12": "bond premium on treasury",
            "13": "bond premium on tax-exempt",
        },
        "coded": set(),
        "other_ids": {"7", "14", "15", "16", "17"},
        "checks": [],
    },
    "1098": {
        "title": r"form 1098\b(?!-)|1098 mortgage", "omb": r"1545-1380",
        "boxes": {
            # box 1 also matches a lender's detail line "<year> MORTGAGE INTEREST RECEIVED ..."
            "1": "((19|20)\\d\\d )?" + MTG + " interest received", "2": "outstanding " + MTG,
            "3": MTG + "( origination|$)", "4": "refund of overpaid",
            "5": MTG + " \\w*nsurance", "6": "points paid", "9": "number of ?propert",
            "10": r"other\b|real \w+ ta\w+", "11": MTG + "( [ao]cquisition|$)",
        },
        "coded": set(),
        "dates": {"3", "11"},  # these boxes hold a date, not an amount
        "counts": {"9"},
        "other_ids": {"7", "8"},
        "checks": [],
    },
    "W2": {
        # the title as a scan may read it: "FormW-2", "Wage and Tax Statoment"
        "title": r"wage and tax st|form ?w-2\b|\bw-2 wage", "omb": r"1545-00(08|29)",
        "boxes": {
            "1": "wages, tips, other comp", "2": "federal income tax withheld",
            "3": "social security wages", "4": "social security tax withheld",
            "5": "medicare wages", "6": "medicare tax withheld", "7": "social security tips",
            "8": "allocated tips", "10": "dependent care", "11": "nonqualified plans",
            "12a": "(see instructions|code|$)", "12b": "(code|$)", "12c": "(code|$)",
            "12d": "(code|$)", "13": r"s ?t ?a ?t ?u ?t ?o ?r ?y|stat\b", "14": r"other\b",
            "15": r"state\b(?! wages| income)", "16": "state wages", "17": "state income tax",
            "18": "local wages", "19": "local income tax",
        },
        "coded": {"12a", "12b", "12c", "12d", "14"},
        "described": {"14"},  # box 14 lines are free text and an amount ("CASDI 1,234.56")
        "code_x": 3,  # in coded boxes, words at the label's left edge are the vertical "C o d e"
        "lower_codes": True,  # a scan reads small code letters as lower case ("w")
        # real box 12 codes only, with a money amount: an address beside the form isn't a line
        "codes": {"12a", "12b", "12c", "12d"},
        "code_set": set(
            "A B C D E F G H J K L M N P Q R S T V W Y Z AA BB DD EE FF GG HH II".split()
        ),
        "right_same_row": True,  # box 20 sits under the wide box 12d: not its right edge
        "sequence": ["12a", "12b", "12c", "12d"],  # a scan may miss "12b": rows placed by height
        "need_id": {"12a", "12b", "12c", "12d", "14"},  # labels too short without their number
        "texts": {"15": r"^[A-Z]{2}$"},  # the state code
        "checkboxes": {
            "13": {
                "statutory": r"s ?t ?a ?t ?u ?t ?o ?r ?y|stat\b",
                "retirement": r"r ?e ?t ?i ?r ?e ?m|ret\b",
                "third-party": r"t ?h ?i ?r ?d|th?i\w?d|3rd|p\w?rty|sick",
            }
        },
        "other_ids": {"9", "20"},
        "checks": [
            ("2 <= 1", ("2", "1"), lambda v, y: v["2"] <= v["1"]),
            ("4 = 6.2% of 3", ("4", "3"), lambda v, y: abs(v["4"] - 0.062 * v["3"]) <= 1),
            (
                "6 = 1.45% of 5 (+0.9% over $200k)",
                ("6", "5"),
                lambda v, y: abs(v["6"] - 0.0145 * v["5"] - 0.009 * max(0, v["5"] - 200000)) <= 1,
            ),
            ("3 + 7 <= the Social Security wage base", ("3", "7"), _w2_base),
        ],
    },
    "1099_R": {
        "title": r"1099-r\b|distributions from pensions", "omb": r"1545-0119",
        "boxes": {
            "1": "gross distribution", "2a": "taxable amount", "2b": "taxable amount",
            "3": "capital gain", "4": "federal income tax", "5": "employee contrib",
            "6": "net unrealized|nua", "7": "distribution", "IRA": "ira/", "8": r"other\b",
            "9a": "(your )?percentage of total", "9b": "total employee contrib",
            "14": "state tax withheld", "15": "state/payer", "16": "state distribution",
        },
        "coded": set(),
        "need_id": {"2a", "2b", "7", "8"},
        "texts": {"7": r"^([1-9]|[A-HJ-NP-UWY]){1,2}$"},  # distribution code(s): "G", "7", "4D"
        "checkboxes": {
            "2b": {
                "not-determined": "taxable amount|not determined",
                "total-distribution": "total",
            },
            "IRA": {"ira-sep-simple": "ira"},
        },
        "other_ids": {"10", "11", "12", "13", "17", "18", "19"},
        "checks": [
            ("2a <= 1", ("2a", "1"), lambda v, y: v["2a"] <= v["1"]),
            ("4 <= 1", ("4", "1"), lambda v, y: v["4"] <= v["1"]),
            (
                "code G (direct rollover): 2a should be 0",
                ("2a", "7"),
                lambda v, y: not ("G" in str(v["7"]) and v["2a"] > 0),
            ),
        ],
    },
    "SSA_1099": {
        "title": r"ssa-1099|social security benefit statement", "omb": None,
        "boxes": {
            "3": "benefits paid", "4": "benefits repaid", "5": "net benefits",
            "6": "voluntary federal income tax",
        },
        "coded": set(),
        "other_ids": {"1", "2", "7", "8"},
        "checks": [
            ("5 = 3 - 4", ("5", "3", "4"), lambda v, y: abs(v["5"] - (v["3"] - v["4"])) < 1)
        ],
    },
    "5498_SA": {
        "title": r"5498-sa\b", "omb": r"1545-151[78]",  # 2025: -1517, the 2026 revision: -1518
        "boxes": {
            "1": "employee(.s)? or self", "2": "total contributions made in",
            "3": "total hsa or archer msa contrib", "4": "rollover contributions",
            "5": "fair market value", "6": r"hsa\b",
        },
        "coded": set(),
        "need_id": {"6"},
        "checkboxes": {"6": {"hsa": r"\bhsa\b", "archer-msa": "archer", "ma-msa": r"\bma [mh]sa"}},
        "tick_beside": {"6"},
        "money_only": True,  # every box is money: a "25" year field beside box 1 isn't its value
        "other_ids": set(),
        "checks": [],
    },
    # 1099-B: the broker's summary table (rows: Form 8949 box letter; columns: amounts), not boxes.
    "1099_B": {
        "title": r"1099-b\b", "omb": r"1545-0715",
        "table": {
            "proceeds": r"(gross )?proceeds", "cost": r"(adjusted )?cost|basis",
            "discount": r"(accrued )?market\b|discount", "wash": r"wash\b",
            "gain": r"(net |realized )?(short-term |long-term )?gain|\(?loss|net gain",
        },
        "boxes": {},
        "coded": set(),
        "other_ids": set(),
        "checks": [],
    },
}  # fmt: skip
# A 1099-B summary row -> its Form 8949 box letter: the letter when printed, else the wording.
B_ROWS = [
    (r"short.?term.*(non-?covered|not reported)", "B"), (r"short.?term.*(covered|reported)", "A"),
    (r"long.?term.*(non-?covered|not reported)", "E"), (r"long.?term.*(covered|reported)", "D"),
    (r"undetermined|unknown term", "X"),
]  # fmt: skip
B_LETTER = re.compile(r"\bbox ([a-fx])\b")
B_TOTAL = re.compile(r"^(grand |sub)?totals?\b")
FORM_ID = re.compile(r"1099-(div|int|b|misc|oid|r)\b|schedule k-1")  # parts of a composite
INSTRUCTIONS = re.compile(
    r"instructions for (recipient|schedule k-1|payer|participant)|partner.s instructions"
)
# tax software often prints whole dollars with a bare trailing period: "19,636." / "0."
AMOUNT = re.compile(r"^[(\-]?\$?[(\-]?(\d{1,3}(,\d{3})+|\d+)(\.\d{0,2})?\)?$")
BOX_ID = re.compile(r"^\d{1,2}[a-z]?$")
MONEY = re.compile(r"[,$]|\.\d\d\)?$")  # formatted like money, not like a box number or a year
YEAR = re.compile(r"^(19|20)\d\d$")
# a ticked checkbox as text or a scan reads it ("冈"/"区" look like a crossed box)
TICKS = {"X", "x", "✓", "✔", "4", "☒", "☑", "⊠", "■", "冈", "区", "図", "凶"}
COUNT = re.compile(r"^\d{1,3}$")
DATE = re.compile(r"^\d{1,2}/\d{1,2}/(\d{2}|\d{4})$")
CODE = re.compile(r"^[A-Z]{1,2}\*?$")
BOX_PREFIX = re.compile(r"^(box\s*)?(\d{1,2}[a-z]?(?!\d))?[\s.\-:]*")
GARBLE = re.compile(r"[@<>=;]")  # a text layer in a font without a Unicode map reads like this
GAP = 8  # points of empty space that split a line into separate segments (form boxes)
FUZZY = 0.8  # similarity needed for a misread label (labels of 10 characters or more)
X_TOL = 1.5  # pdfplumber: letters closer than this are one word
QUALITY = 0.9  # a text layer with fewer clean words than this is garbled: not read


def _mid(w: dict[str, Any]) -> float:
    return (w["top"] + w["bottom"]) / 2


# --- page words ---------------------------------------------------------------------------------
def text_quality(text: str) -> float:
    """Share of words free of garble characters (1.0: clean). A near-empty page scores 0."""
    tokens = text.split()
    if len(tokens) < 20:
        return 0.0
    return 1 - sum(bool(GARBLE.search(t)) for t in tokens) / len(tokens)


def _dark(color: Any) -> bool:  # noqa: ANN401 - pdfplumber's colour: gray, RGB or CMYK
    c = (
        tuple(color)
        if isinstance(color, list | tuple)
        else (color,)
        if isinstance(color, int | float)
        else ()
    )
    return (len(c) in (1, 3) and max(c) < 0.3) or (len(c) == 4 and c[3] > 0.7)


def _filled_boxes(page: Any) -> list[Word]:  # noqa: ANN401 - a pdfplumber page
    """Small filled dark squares (a checkbox ticked as a drawing) as the tick word "■"."""
    out = []
    for r in page.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if (
            r.get("fill")
            and 4 <= w <= 16
            and 4 <= h <= 16
            and abs(w - h) < 3
            and _dark(r.get("non_stroking_color"))
        ):
            out.append({"text": "■", "x0": r["x0"], "x1": r["x1"], "top": r["top"],
                        "bottom": r["bottom"]})  # fmt: skip
    return out


def scan_words(page: Page) -> list[Word]:
    """A recognised page's words (`pdfdoc.Page`, as `ocr` gives them)."""
    return [
        {"text": w.text, "x0": w.box.x0, "x1": w.box.x1, "top": w.box.y0, "bottom": w.box.y1}
        for w in page.words
    ]


def segments(words: list[Word]) -> list[Seg]:
    """Words grouped into lines, the lines split at wide gaps: one segment per form box."""
    lines: list[dict[str, Any]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        mid = _mid(w)
        for line in lines:
            if abs(line["mid"] - mid) < 0.6 * (w["bottom"] - w["top"]):
                line["words"].append(w)
                break
        else:
            lines.append({"mid": mid, "words": [w]})
    segs = []
    for line_no, line in enumerate(sorted(lines, key=lambda ln: ln["mid"])):
        current: list[Word] = []
        ws = sorted(line["words"], key=lambda w: w["x0"])
        for i, w in enumerate(ws):
            new_box = (
                BOX_ID.match(w["text"].rstrip("."))
                and i + 1 < len(ws)
                and ws[i + 1]["text"][:1].isupper()
            )
            if current and (w["x0"] - current[-1]["x1"] > GAP or new_box):
                segs.append(_segment(current, line_no))
                current = []
            current.append(w)
        segs.append(_segment(current, line_no))
    return segs


def _segment(words: list[Word], line_no: int) -> Seg:
    return {
        "words": words, "line": line_no, "text": " ".join(w["text"] for w in words),
        "x0": min(w["x0"] for w in words), "x1": max(w["x1"] for w in words),
        "top": min(w["top"] for w in words), "bottom": max(w["bottom"] for w in words),
    }  # fmt: skip


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("\u2019", "'")).strip()


def to_number(token: str) -> float:
    neg = token.startswith(("(", "-")) or token.endswith(")") or "$(" in token or "$-" in token
    value = float(re.sub(r"[^\d.]", "", token))
    return -value if neg else value


# --- labels and values --------------------------------------------------------------------------
def find_labels(form: dict[str, Any], segs: list[Seg]) -> dict[str, Seg]:
    """Box id -> the segment holding its label: the pattern first, then nearly (a misread scan)."""
    found: dict[str, Seg] = {}
    used: set[int] = set()
    stripped = []
    for s in segs:
        t = norm(s["text"])
        m = BOX_PREFIX.match(t)
        stripped.append((t[m.end() :], m.group(2)) if m else (t, None))
    for box, pattern in form["boxes"].items():
        hits = [
            i
            for i, (t, box_id) in enumerate(stripped)
            if i not in used and box_id in (None, box) and re.match(pattern, t)
        ]
        # a label carrying its box number, in the segment or just left of it, first
        confirmed = [
            i
            for i in hits
            if stripped[i][1] == box
            or any(
                norm(o["text"]).rstrip(".") == box
                and o["line"] == segs[i]["line"]
                and 0 <= segs[i]["x0"] - o["x1"] < 30
                for o in segs
            )
        ]
        if box in form.get("need_id", ()):  # short labels: only with their box number
            hits = confirmed
        if hits:
            i = (confirmed or hits)[0]
            found[box] = segs[i]
            used.add(i)
    for box, pattern in form["boxes"].items():
        label = re.sub(r"\\|\(\?!.*?\)", "", pattern.replace(MTG, "mortgage"))
        if box in found or len(label) < 10 or box in form.get("need_id", ()):
            continue
        best = max(
            (
                (difflib.SequenceMatcher(None, label, t[: len(label)]).ratio(), i)
                for i, (t, box_id) in enumerate(stripped)
                if i not in used and box_id in (None, box)
            ),
            default=(0.0, -1),
        )
        if best[0] >= FUZZY:
            found[box] = segs[best[1]]
            used.add(best[1])
    return found


def box_starts(segs: list[Seg], form: dict[str, Any]) -> set[int]:
    """Segments that begin one of the form's boxes ("8 Foreign country…", or a box number just
    left of its label). A lone small amount ("10") isn't a box number: no label follows it."""
    ids = form["boxes"].keys() | form["other_ids"]
    out = set()
    for seg in segs:
        ws = seg["words"]
        if ws[0]["text"].rstrip(".") not in ids:
            continue
        if len(ws) > 1:
            if ws[1]["text"][:1].isupper():
                out.add(id(seg))
        elif ws[0]["text"] in form.get("need_id", ()) and ws[0]["text"][-1].isalpha():
            out.add(id(seg))  # a bare "12c" is a label
        elif any(
            o["line"] == seg["line"]
            and 0 <= o["x0"] - seg["x1"] < 30
            and re.match(r"[A-Za-z]{2}", o["text"])
            for o in segs
        ):
            out.add(id(seg))
    return out


def box_area(
    label: Seg, labels: dict[str, Seg], segs: list[Seg], starts: set[int], same_row: bool = False
) -> tuple[float, float, float, float]:
    """The area below a label: down to the next box beneath it, right to the next box column.
    `same_row`: only labels in the label's own row, just under it or above set the right edge."""
    bounds = [s for s in labels.values() if s is not label] + [s for s in segs if id(s) in starts]

    # the row of boxes under this one (1098: boxes 2 and 3 under the wide box 1) is no right edge
    under = [
        s["top"]
        for s in labels.values()
        if abs(s["x0"] - label["x0"]) < 10 and label["bottom"] <= s["top"] < label["bottom"] + 40
    ]

    def near(s: Seg) -> bool:
        if same_row:
            return label["top"] - 40 < s["top"] < label["bottom"] + 20
        next_row = any(abs(s["top"] - t) < 4 for t in under)
        return abs(s["top"] - label["top"]) < 40 and not next_row

    right = min(
        (
            s["x0"]
            for s in bounds
            if s["x0"] > label["x0"] + 40 and s["x0"] > label["x1"] - 40 and near(s)
        ),
        default=1e9,
    )
    below = min(
        (
            s["top"]
            for s in bounds
            if s["top"] > label["bottom"] - 1 and s["x1"] > label["x0"] and s["x0"] < right
        ),
        default=label["bottom"] + 60,
    )
    return label["x0"] - 25, label["bottom"] - 2, right, below


def same_line_amount(
    label: Seg, labels: dict[str, Seg], segs: list[Seg], right: float, starts: set[int]
) -> str | None:
    """The amount right of the label on its own line (statement style: "label ..... 1,234.56")."""
    label_ids = {id(s) for s in labels.values()}
    tokens = [w["text"] for w in label["words"]]
    for s in sorted(
        (s for s in segs if s["line"] == label["line"] and label["x1"] < s["x0"] < right),
        key=lambda s: s["x0"],
    ):
        if id(s) in label_ids or id(s) in starts:
            break
        tokens += [w["text"] for w in s["words"]]
    amounts = [t for t in tokens if AMOUNT.match(t) and MONEY.search(t)]
    return amounts[-1] if amounts else None


def area_rows(
    area: tuple[float, float, float, float], words: list[Word], skip: set[int]
) -> list[list[Word]]:
    """The words inside the area (less the skipped ones), in rows by height."""
    left, top, right, bottom = area
    inside = sorted(
        (
            w
            for w in words
            if id(w) not in skip
            and w["x0"] >= left
            and w["x1"] <= right + 2
            and top < _mid(w) < bottom
        ),
        key=_mid,
    )
    rows: list[tuple[float, list[Word]]] = []
    for w in inside:
        if rows and _mid(w) - rows[-1][0] < 6:
            rows[-1][1].append(w)
        else:
            rows.append((_mid(w), [w]))
    return [sorted(r, key=lambda w: w["x0"]) for _, r in rows]


def column_sum(
    label: Seg, labels: dict[str, Seg], words: list[Word], area: tuple[float, ...]
) -> tuple[float, list[float]] | None:
    """A statement with the box labels side by side in one row and a row of values per account
    below (a bank's 1099-INT): (total, the rows' amounts) when the column holds several."""
    if sum(s["line"] == label["line"] for s in labels.values()) < 3:
        return None
    left, _, right, _ = area
    below = sorted(
        (
            w
            for w in words
            if w["x0"] >= left and w["x1"] <= right + 2 and w["top"] > label["bottom"]
        ),
        key=lambda w: w["top"],
    )
    parts: list[float] = []
    last = label["bottom"]
    for w in below:
        if w["top"] - last > 60 or re.search(r"[A-Za-z]{3}", w["text"]):
            break
        if AMOUNT.match(w["text"]) and MONEY.search(w["text"]):
            if any(
                re.fullmatch(r"(?i)totals?:?", o["text"]) and abs(_mid(o) - _mid(w)) < 4
                for o in words
            ):
                return (to_number(w["text"]), parts) if parts else None  # the statement's total
            parts.append(to_number(w["text"]))
            last = w["top"]
    return (round(sum(parts), 2), parts) if len(parts) > 1 else None


def _ticked(
    box: str, form: dict[str, Any], label: Seg, segs: list[Seg], words: list[Word]
) -> list[str]:
    """The names of a box's ticked checkboxes, e.g. ["retirement"]."""
    beside = box in form.get("tick_beside", ())  # names stacked, each box right of its name
    subs = {}
    for name, pattern in form["checkboxes"][box].items():
        sub = next(
            (
                s
                for s in segs
                if s["x0"] >= label["x0"] - 2
                and re.search(pattern, norm(s["text"]))
                and (
                    label["top"] - 2 <= s["top"] < label["top"] + 40
                    if beside
                    else abs(s["top"] - label["top"]) < 8
                )
            ),
            None,
        )
        if sub:  # anchored on the matching word when a scan joined several names in one segment
            word = next((w for w in sub["words"] if re.search(pattern, norm(w["text"]))), None)
            subs[name] = {**sub, "x0": word["x0"], "x1": word["x1"]} if word else sub
    ticked = set()
    # a "4" (a filled checkbox as text) counts only on its own: "4 Federal …" is a box number
    alone = {id(sg["words"][0]) for sg in segs if len(sg["words"]) == 1}
    for w in (w for w in words if w["text"] in TICKS and (w["text"] != "4" or id(w) in alone)):
        if beside:  # on its name's line, to the right
            near = [
                n for n, sub in subs.items() if abs(_mid(w) - _mid(sub)) < 5 and w["x0"] > sub["x0"]
            ]
            pick = min(near, key=lambda n: abs(_mid(w) - _mid(subs[n])), default=None)
        else:  # under (or just right of) its name: the nearest name starting at or before it
            near = [
                n
                for n, sub in subs.items()
                if sub["x0"] - 8 <= w["x0"] <= sub["x1"] + 70
                and sub["top"] + 5 < w["top"] < sub["bottom"] + 25
            ]
            pick = max(
                (n for n in near if subs[n]["x0"] <= w["x0"] + 8),
                key=lambda n: subs[n]["x0"],
                default=None,
            )
        if pick:
            ticked.add(pick)
    return sorted(ticked)


def _coded(
    box: str, form: dict[str, Any], label: Seg, rows: list[list[Word]]
) -> list[dict[str, Any]]:
    """A coded box's lines: [{"code", "amount"}], "statement": True where it says STMT."""
    items = []
    for row in rows:
        row = [w for w in row if w["x0"] > label["x0"] + form.get("code_x", -1e9)]
        texts = [w["text"] for w in row]
        code = next((t for t in texts if CODE.match(t) and t != "STMT"), None)
        if code is None and form.get("lower_codes"):
            code = next((t.upper() for t in texts if re.fullmatch(r"[a-z]{1,2}", t)), None)
        if box in form.get("described", ()):
            code = " ".join(t for t in texts if not AMOUNT.match(t) and t != "$") or None
        if box in form.get("codes", ()):
            code = code if code in form["code_set"] else None
            if code is None or not any(AMOUNT.match(t) and MONEY.search(t) for t in texts):
                continue
        amount = next(
            (
                w["text"]
                for w in reversed(row)
                if AMOUNT.match(w["text"])
                and w["x1"] > label["x0"]
                and (box not in form.get("codes", ()) or MONEY.search(w["text"]))
            ),
            None,
        )
        if code or amount:
            item = {
                "code": code,
                "amount": to_number(amount) if amount else None,
                "_mid": sum(_mid(w) for w in row) / len(row),
            }
            if "STMT" in texts:
                item["statement"] = True  # the amount is on an attached statement
            items.append(item)
    return items


def extract_box(
    box: str,
    form: dict[str, Any],
    labels: dict[str, Seg],
    segs: list[Seg],
    words: list[Word],
    starts: set[int],
) -> Any:  # noqa: ANN401 - a number, a string, a list, or (total, parts)
    label = labels[box]
    area = box_area(label, labels, segs, starts, form.get("right_same_row", False))
    skip = {id(w) for s in segs if id(s) in starts or s is label for w in s["words"]}
    rows = area_rows(area, words, skip)
    if box in form.get("checkboxes", {}):
        return _ticked(box, form, label, segs, words)
    if box in form["coded"]:
        return _coded(box, form, label, rows)
    plain = (
        DATE
        if box in form.get("dates", ())
        else COUNT
        if box in form.get("counts", ())
        else re.compile(form["texts"][box])
        if box in form.get("texts", {})
        else None
    )
    if plain:  # a date or a count, not money: the same line first, then the box's area
        others = {id(s) for s in labels.values()} | starts
        line = [
            w["text"]
            for s in segs
            if s["line"] == label["line"] and s["x0"] > label["x0"] and id(s) not in others
            for w in s["words"]
        ]
        token = next(
            (t for t in line + [w["text"] for row in rows for w in row] if plain.match(t)), None
        )
        return float(token) if plain is COUNT and token else token
    if (total := column_sum(label, labels, words, area)) is not None:
        return total
    token = same_line_amount(label, labels, segs, area[2], starts)
    if token is None:  # money-formatted first, so a printed tax year or line number can't win
        # an amount reaches past the label's start; one ending left of it is the column before's;
        # "NONE" in a box (SSA-1099) is a zero, and stops the search reaching the box below
        candidates = [
            "0.00" if w["text"].upper() == "NONE" else w["text"]
            for row in rows
            for w in row
            if (AMOUNT.match(w["text"]) or w["text"].upper() == "NONE") and w["x1"] > label["x0"]
        ]
        # a plain number ("500"), never out of a sentence ("... entered in box 8.")
        ids = form["boxes"].keys() | form["other_ids"]
        edges = [s["x0"] for s in labels.values()]
        plain_numbers = [
            w["text"]
            for row in rows
            if sum(bool(re.search(r"[A-Za-z]{2}", w["text"])) for w in row) < 3
            for w in row
            if AMOUNT.match(w["text"])
            and w["x1"] > label["x0"]
            and not (w["text"] in ids and any(abs(w["x0"] - e) < 3 for e in edges))  # a box no.
        ]
        token = next(
            (t for t in candidates if MONEY.search(t)),
            None
            if form.get("money_only")
            else next((t for t in plain_numbers if not YEAR.match(t)), None),
        )
    return to_number(token) if token else None


def table_rows(
    form: dict[str, Any], segs: list[Seg]
) -> tuple[dict[str, dict[str, float]], dict[str, Seg]]:
    """A 1099-B summary table: ({row letter: {column: amount}}, the column headers found). Each
    amount goes to the header it sits under. A row label without amounts on its line (a wrapped
    label) takes the amounts of the line next to it."""
    cands = []
    for s in segs:
        t = norm(s["text"])
        m = BOX_PREFIX.match(t)
        t = t[m.end() :] if m else t
        col = next((c for c, pattern in form["table"].items() if re.match(pattern, t)), None)
        if col:
            cands.append((col, s))
    # the header row: the first proceeds header with 2+ other columns within 20 pt; per column the
    # candidate nearest it in height (a title such as "Realized gain/loss summary" sits above)
    headers: dict[str, Seg] = {}
    for col, p in cands:
        near = {
            c: s
            for c, s in sorted(cands, key=lambda cs: -abs(cs[1]["top"] - p["top"]))
            if abs(s["top"] - p["top"]) < 20 and s["x0"] > p["x0"] - 20
        }
        if col == "proceeds" and len(near) >= 3:
            headers = near
            break
    if len(headers) < 3:
        return {}, headers
    lines: dict[int, list[Seg]] = {}
    for s in segs:
        lines.setdefault(s["line"], []).append(s)
    head_bottom = max(h["bottom"] for h in headers.values())
    left = min(h["x0"] for h in headers.values()) - 30

    def amounts(line: list[Seg]) -> list[Word]:
        return [
            w
            for s in line
            for w in s["words"]
            if w["x0"] >= left and AMOUNT.match(w["text"]) and MONEY.search(w["text"])
        ]

    def key(line: list[Seg]) -> str | None:
        text = norm(" ".join(s["text"] for s in line if s["x0"] < left + 30))
        if B_TOTAL.match(text):
            return None
        letters = set(B_LETTER.findall(text))
        k = (
            ("X" if len(letters) > 1 else letters.pop().upper())  # "Box B or Box E": unknown term
            if letters
            else next((k for pat, k in B_ROWS if re.search(pat, text)), None)
        )
        return k and k + ("_ordinary" if "ordinary" in text else "")

    def dist(w: Word, h: Seg) -> float:
        overlap = min(w["x1"], h["x1"]) - max(w["x0"], h["x0"])
        return -overlap if overlap > 0 else abs((w["x0"] + w["x1"]) / 2 - (h["x0"] + h["x1"]) / 2)

    rows: dict[str, dict[str, float]] = {}
    order = sorted(lines)
    for i, n in enumerate(order):
        line = lines[n]
        if line[0]["top"] <= head_bottom or (k := key(line)) is None or k in rows:
            continue
        ws = amounts(line)
        if not ws:  # a wrapped label: the amounts on the next line that has no label of its own
            near_lines = [
                lines[m]
                for m in order[max(i - 1, 0) : i + 2]
                if m != n
                and key(lines[m]) is None
                and abs(lines[m][0]["top"] - line[0]["top"]) < 14
            ]
            ws = next((amounts(ln) for ln in near_lines if amounts(ln)), [])
        if not ws:
            continue
        row: dict[str, float] = {}
        for w in ws:
            col = min(headers, key=lambda c, w=w: dist(w, headers[c]))
            if dist(w, headers[col]) < 60 and col not in row:
                row[col] = to_number(w["text"])
        rows[k] = row
    return rows, headers


def table_check(row: dict[str, float]) -> bool:
    """Gain = proceeds − cost (realized), or with the wash sale and market discount, within $1."""
    if not {"proceeds", "cost", "gain"} <= row.keys():
        return True
    base = row["proceeds"] - row["cost"]
    wash, discount = row.get("wash", 0), row.get("discount", 0)
    return any(
        abs(base + extra - row["gain"]) <= 1 for extra in (0, wash, discount, wash + discount)
    )


def place_sequence(
    form: dict[str, Any], boxes: dict[str, dict], labels: dict[str, Seg], page_no: int
) -> None:
    """Evenly stacked boxes (W-2 12a–12d) when a scan missed some of their labels: a found box's
    area then holds the missing ones' lines. Each line goes to the slot it sits in; those placed
    are marked inferred."""
    seq = form.get("sequence", [])
    missing = [b for b in seq if boxes.get(b, {}).get("status") == "missing"]
    if not missing:
        return
    first, last = labels.get(seq[0]), labels.get(seq[-1])
    pitch = (
        (last["top"] - first["top"]) / (len(seq) - 1)
        if first and last and last["top"] > first["top"] + 10
        else None
    )
    for i, box in enumerate(seq):
        b = boxes.get(box, {})
        if b.get("page") != page_no or not isinstance(b.get("value"), list) or len(b["value"]) < 2:
            continue
        keep = []
        for j, item in enumerate(b["value"]):
            if pitch and first:
                slot = min(len(seq) - 1, max(0, int((item["_mid"] - first["top"]) / pitch)))
            else:
                slot = i + j
            target = seq[min(slot, len(seq) - 1)]
            if target == box or target not in missing:
                keep.append(item)
                continue
            boxes[target] = {"value": [item], "page": page_no, "inferred": True,
                             "status": "ok" if item["code"] else "incomplete"}  # fmt: skip
            missing.remove(target)
        b["value"] = keep
        b["status"] = (
            "blank" if not keep else "ok" if all(it["code"] for it in keep) else "incomplete"
        )


# --- which forms are on a page ------------------------------------------------------------------
def regions(segs: list[Seg]) -> list[tuple[float, float, float, float, str]] | None:
    """Each form's part of a page holding several (a broker's composite): (left, top, right,
    bottom, key). A part runs down its column from its header to the next one."""
    marks = []
    for s in segs:
        m = FORM_ID.search(norm(s["text"]))
        if m:
            marks.append((s["top"], s["x0"], m.group(0)))
    if len({k for _, _, k in marks}) <= 1:
        return None
    out = []
    for top, x0, key in marks:
        right = min((x for _, x, _ in marks if x > x0 + 50), default=1e9)
        bottom = min((t for t, x, _ in marks if t > top + 1 and abs(x - x0) < 50), default=1e9)
        out.append((x0 - 30, top - 1, right, bottom, key))
    return out


def form_segments(name: str, segs: list[Seg], parts: list | None) -> list[Seg]:
    if parts is None:
        return segs
    boxes = [r[:4] for r in parts if re.fullmatch(FORMS[name]["title"], r[4])]
    return [
        s for s in segs if any(x0 <= s["x0"] < x1 and t <= s["top"] < b for x0, t, x1, b in boxes)
    ]


def analyse_page(segs: list[Seg]) -> dict[str, tuple[str, Any, list[Seg]]]:
    """{form: ("core" or "supporting", labels or table rows, its segments)} for each form whose
    title or OMB number is on the page."""
    text = norm(" ".join(s["text"] for s in segs))
    parts = regions(segs)
    out: dict[str, tuple[str, Any, list[Seg]]] = {}
    instructions = bool(INSTRUCTIONS.search(text))
    for name, form in FORMS.items():
        omb = bool(form["omb"] and re.search(form["omb"], text))
        title = bool(re.search(form["title"], text))
        if not (title or omb):
            continue
        fsegs = form_segments(name, segs, parts)
        if "table" in form:  # a summary page: headers, a row with amounts, "summary" in its text
            rows, _ = table_rows(form, fsegs)
            core = bool(rows) and "summary" in text and not instructions
            out[name] = ("core" if core else "supporting", rows, fsegs)
            continue
        labels = find_labels(form, fsegs)
        # forms and substitute statements print the OMB number, instruction pages don't; a form
        # printing none (SSA-1099): its title and labels instead
        anchor = omb if form["omb"] else title and len(labels) >= 3
        core = len(labels) >= min(4, len(form["boxes"]) - 1) and anchor and not instructions
        out[name] = ("core" if core else "supporting", labels, fsegs)
    return out


# --- a document ---------------------------------------------------------------------------------
def run_checks(form: dict[str, Any], boxes: dict[str, dict], year: int | None = None) -> None:
    def number(b: str) -> Any:  # noqa: ANN401
        v = boxes[b]["value"] if b in boxes else None
        return v if isinstance(v, int | float | str) else 0

    values = {b: number(b) or 0 for b in form["boxes"]}
    test: Callable[[dict[str, Any], int | None], bool]
    for desc, involved, test in form["checks"]:
        if any(boxes.get(b, {}).get("value") for b in involved) and not test(values, year):
            for b in involved:
                if b in boxes and boxes[b]["status"] == "ok":
                    boxes[b]["status"] = "check-failed"
                    boxes[b]["check"] = desc


def _wanted(text: str) -> bool:
    """Might this page hold one of the forms (its title or OMB number)? From the fast text."""
    t = norm(text)
    return any(
        re.search(f["title"], t) or (f["omb"] and re.search(f["omb"], t)) for f in FORMS.values()
    )


def page_words(data: bytes | None, scans: list[Page] | None = None) -> list[tuple[int, list[Word]]]:
    """(page number, words) of each page that may hold a form: a recognised scan's words, else
    the text layer's (a garbled text layer is left out: text recognition reads it)."""
    scanned = {p.number: p for p in scans or []}
    out = [(n, scan_words(p)) for n, p in sorted(scanned.items()) if _wanted(p.text)]
    if data is None:
        return out
    try:
        pdf = Pdf(data, flatten=True)  # form-field values become page text
    except ValueError:
        return out  # password-protected or unreadable
    try:
        todo = [i for i in range(len(pdf)) if i + 1 not in scanned and _wanted(pdf.text(i))]
        if not todo:
            return out
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf.data)) as plumber:
            for i in todo:
                if i >= len(plumber.pages):  # a damaged page list: pdfium sees more pages
                    break
                page = plumber.pages[i]
                try:
                    if text_quality(page.extract_text(x_tolerance=X_TOL) or "") >= QUALITY:
                        words = page.extract_words(x_tolerance=X_TOL) + _filled_boxes(page)
                        out.append((i + 1, words))
                finally:
                    page.close()
    finally:
        pdf.close()
    return sorted(out, key=lambda nw: nw[0])


@perf.timed("forms.read", many=True)
def read(data: bytes | None, scans: list[Page] | None = None, year: int | None = None) -> dict:
    """Each known form's main pages and boxes: {form: {"core_pages", "boxes": {box: {"value",
    "page", "status", …}}}}. `scans`: the pages recognised on a scan, read instead of the PDF's
    own text; `year`: the tax year, for the checks that depend on it."""
    result: dict[str, dict[str, Any]] = {}
    for n, words in page_words(data, scans):
        segs = segments(words) if words else []
        for name, (status, labels, fsegs) in analyse_page(segs).items():
            entry = result.setdefault(name, {"core_pages": [], "boxes": {}})
            if status != "core":
                continue
            entry["core_pages"].append(n)
            form = FORMS[name]
            if "table" in form:
                if not entry["boxes"]:  # the first summary page wins
                    for letter, row in labels.items():
                        ok = table_check(row)
                        for col in form["table"]:
                            v = row.get(col)
                            entry["boxes"][f"{letter}_{col}"] = {
                                "value": v, "page": n,
                                "status": "blank" if v is None else "ok" if ok else "check-failed",
                            }  # fmt: skip
                            if not ok and v is not None:
                                entry["boxes"][f"{letter}_{col}"]["check"] = (
                                    "gain = proceeds - cost (+ adjustments)"
                                )
                continue
            starts = box_starts(fsegs, form)
            fwords = [w for g in fsegs for w in g["words"]]
            for box in form["boxes"]:
                if box in entry["boxes"] and entry["boxes"][box]["status"] != "missing":
                    continue  # the first main page wins
                if box not in labels:
                    entry["boxes"][box] = {"value": None, "page": n, "status": "missing"}
                    continue
                value = extract_box(box, form, labels, fsegs, fwords, starts)
                parts = None
                if isinstance(value, tuple):  # summed over several accounts' rows
                    value, parts = value
                status = "blank" if value in (None, []) else "ok"
                if isinstance(value, list) and any(
                    isinstance(i, dict) and i["code"] is None for i in value
                ):
                    status = "incomplete"  # an amount without its code letter
                entry["boxes"][box] = {"value": value, "page": n, "status": status}
                if parts:
                    entry["boxes"][box]["parts"] = parts
            place_sequence(form, entry["boxes"], labels, n)
            for b in entry["boxes"].values():
                for item in b["value"] if isinstance(b["value"], list) else []:
                    if isinstance(item, dict):
                        item.pop("_mid", None)
    for name, entry in result.items():
        run_checks(FORMS[name], entry["boxes"], year)
    return {k: v for k, v in result.items() if v["core_pages"]}
