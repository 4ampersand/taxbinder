"""Who sent a form, and the account it's for, read by their place on the form.

On an IRS form the issuer's name is the first line under its caption ("PAYER'S name, street
address, …", "c Employer's name", "B Partnership's name"). A statement the issuer designed itself,
or a letter, has no caption: there the name is the letterhead, the largest line at the top. The
values read here are kept in memory only; an account number or EIN is never logged.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher

from taxbinder.pdfdoc import Box, Page, Pdf

# Captions above the issuer's name, as printed (the start of the caption, after a box letter).
ISSUER = re.compile(
    r"^(?:[a-z]\s+)?(?:payer's|employer's|partnership's|corporation's|estate's or trust's"
    r"|trustee's or issuer's|recipient's/lender's|payer's/trustee's|trustee's|filer's"
    r"|lender's|issuer's)\s+name\b",
    re.IGNORECASE,
)
# Captions above the issuer's EIN; and an EIN printed after a label in a letter or statement.
ISSUER_TIN = re.compile(
    r"^(?:[a-z]\s+)?(?:payer's|employer|partnership's|corporation's|estate's or trust's"
    r"|trustee's or issuer's|recipient's/lender's|payer's/trustee's|trustee's|filer's)"
    r"\s+(?:tin|federal id|employer identification number|identification number)\b",
    re.IGNORECASE,
)
# Captions above the recipient's name: who the form is for.
RECIPIENT = re.compile(
    r"^(?:[a-z]\s+)?(?:(?:recipient's|employee's(?: first)?|participant's|payer's/borrower's"
    r"|borrower's|beneficiary's|shareholder's|partner's)\s+name\b|name, address.*\bfor partner\b"
    r"|box 1\.\s*name\b|prepared for\b)",
    re.IGNORECASE,
)
RECIPIENT_IN_TEXT = re.compile(r"^(?:owner|taxpayer|account holder)s?:\s*(.+)$|^dear (.+?),\s*$",
                               re.IGNORECASE | re.MULTILINE)  # fmt: skip
# A person's number, full or masked: only its last 4 digits are ever kept.
SSN = re.compile(r"(?<![\d-])(?:\d{3}|[x*•]{3})-(?:\d{2}|[x*•]{2})-(\d{4})(?![\d-])", re.I)
EIN = re.compile(r"(?<![\d-])(\d{2}-\d{7})(?![\d-])")
EIN_LABELLED = re.compile(r"(?:federal (?:tax )?id|\bein\b|\btin\b)\D{0,12}(\d{2}-\d{7})", re.I)
ACCOUNT_CAPTION = re.compile(r"^account (?:number|no\.?)\b", re.IGNORECASE)
ACCOUNT_IN_TEXT = re.compile(
    r"\b(?:account|acct)\s*(?:ending in|number|no\.?|#)\s*[:#]?\s*([x*•…\d][\dx*•…\- ]{2,22}\d)",
    re.IGNORECASE,
)
# A line at the top that is the form's title, not who sent it.
TITLE = re.compile(
    r"\bform\b|\bschedule\b|\bcopy\b|\bcorrected\b|statement|\b(?:1099|1098|5498|w-?2|k-1)\b"
    r"|\b20\d{2}\b|\w's share\b|\binformation\b|\bdistributions?\b|\binterest income\b"
    r"|\bdividends\b|\bpayments? from\b",
    re.IGNORECASE,
)
KNOWN_ISSUERS = {"ssa_1099": "Social Security Administration"}
# Kept as capitals when a name printed in capitals is turned into Title Case.
KEEP_CAPS = {"LLC", "LP", "LLP", "PLLC", "NA", "N.A.", "FSB", "HSA", "IRA", "USA", "US", "U.S.",
             "II", "III", "IV", "PC", "PA", "CPA", "JP", "BNY"}  # fmt: skip
SMALL = {"of", "and", "the", "for", "in", "on", "at", "by", "to"}


@dataclass
class Parties:
    issuer: str | None = None  # the issuer's name, cleaned up
    issuer_tin: str | None = None  # as printed (an EIN); encrypted when stored
    account: str | None = None  # as printed; encrypted when stored
    recipient: str | None = None  # who it's for, as printed
    recipient_last4: str | None = None  # the last 4 of their number, never more


def clean_name(name: str) -> str:
    """'LARKMOOR SECURITIES LLC,' → 'Larkmoor Securities LLC'; spacing and stray punctuation."""
    name = " ".join(name.split()).strip(" ,;:-–*")
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 3:
        words = []
        for i, w in enumerate(name.split(" ")):
            if w in KEEP_CAPS:
                words.append(w)
            elif i and w.lower() in SMALL:
                words.append(w.lower())
            else:
                words.append("-".join(p[:1] + p[1:].lower() for p in w.split("-")))
        name = " ".join(words)
    return name


def _below(lines: list[tuple[Box, str]], caption: Box, reach: float = 26) -> list[str]:
    """Lines just under a caption, in its column, top to bottom."""
    return [
        text
        for box, text in lines
        # below the caption's middle: a scan's boxes are loose and overlap the next line
        if (caption.y0 + caption.y1) / 2 < box.y0 <= caption.y1 + reach
        and abs(box.x0 - caption.x0) <= 12
    ]


# Captions as words, for a scan's reading slips ("c Employor's name", "REGIPIENTS name"): each
# with the role of the value under it. The nearest caption wins, so "RECIPIENT'S/LENDER'S name"
# (the lender) is never taken for "RECIPIENT'S name".
CAPTIONS = [
    *(("issuer", f"{who} name") for who in (
        "payers", "employers", "partnerships", "corporations", "estates or trusts",
        "trustees or issuers", "recipients/lenders", "payers/trustees", "trustees", "filers",
    )),
    *(("issuer_tin", f"{who} {what}") for who in (
        "payers", "trustees or issuers", "recipients/lenders", "payers/trustees", "trustees",
    ) for what in ("tin", "federal id")),
    ("issuer_tin", "employer identification number"),
    ("issuer_tin", "partnerships employer identification number"),
    ("issuer_tin", "corporations employer identification number"),
    *(("recipient", f"{who} name") for who in (
        "recipients", "employees first", "participants", "payers/borrowers", "beneficiarys",
        "shareholders", "partners",
    )),
    ("recipient", "box 1. name"),
    ("recipient", "prepared for"),
    ("account", "account number"),
]  # fmt: skip
ADDRESS = re.compile(
    r"[,.]\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|\bsuite\b|\bp\.?\s?o\.? box\b|\bstreet\b",
    re.IGNORECASE,
)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9/. ]", "", text.lower()).split())


def _fuzzy_role(text: str) -> str | None:
    """The role of a caption read with slips, or None: the closest caption at the start of the
    line (after a box letter), if it's close enough."""
    words = _words(text).split(" ")
    if len(words) > 1 and len(words[0]) == 1:
        words = words[1:]  # the box's letter: "c", "B"
    best, role = 0.0, None
    for r, caption in CAPTIONS:
        n = len(caption.split(" "))
        ratio = SequenceMatcher(None, " ".join(words[:n]), caption).ratio()
        if ratio > best:
            best, role = ratio, r
    return role if best >= 0.85 else None


def _role(text: str, fuzzy: bool) -> str | None:
    """What a caption line labels: issuer, issuer_tin, recipient, account; None if no caption."""
    for role, pattern in (
        ("issuer", ISSUER), ("issuer_tin", ISSUER_TIN), ("recipient", RECIPIENT),
        ("account", ACCOUNT_CAPTION),
    ):  # fmt: skip
        if pattern.match(text):
            return role
    return _fuzzy_role(text) if fuzzy else None


def _plausible(name: str, fuzzy: bool = False) -> bool:
    """A name, not an address line, a number, or another caption."""
    return (
        len(name) >= 3
        and not name[0].isdigit()
        and sum(c.isalpha() for c in name) >= 3
        and _role(name, fuzzy) is None
        and not ADDRESS.search(name)
        and not re.search(
            r"\b(?:tin|street address|zip code|identification number)\b", name, re.IGNORECASE
        )
    )


def _letterhead(page: Page, lines: list[tuple[Box, str]]) -> str | None:
    """The largest line in the top fifth of the first page, when it's clearly larger than all
    the other text but the form's own title."""
    text = [(b, t) for b, t in lines if not TITLE.search(t)]
    if len(text) < 2:
        return None
    height = lambda b: b.y1 - b.y0  # noqa: E731
    best_box, best = max(text, key=lambda bt: (height(bt[0]), -bt[0].y0))
    rest = [height(b) for b, t in text if t != best]
    typical = statistics.median(height(b) for b, _ in lines)
    if (
        best_box.y0 >= page.height * 0.2
        or height(best_box) < max(1.3 * typical, max(rest) + 1)
        or not _plausible(best)
    ):
        return None
    return best


def _scan_letterhead(page: Page, lines: list[tuple[Box, str]]) -> str | None:
    """On a scan, text sizes are too rough to compare: the first line from the top that isn't
    the form's title, a caption or an address."""
    for box, text in lines:
        if box.y0 >= page.height * 0.2:
            return None
        if not TITLE.search(text) and _plausible(text, fuzzy=True):
            return text
    return None


def read_parties(data: bytes, doc_type: str | None, pages: list[Page] | None = None) -> Parties:
    """The issuer's name and EIN, the account number and the recipient on a document's first
    page. `pages`: recognised from a scan (captions then allow for reading slips)."""
    out = Parties(issuer=KNOWN_ISSUERS.get(doc_type or ""))
    fuzzy = bool(pages)
    if pages:
        page = pages[0]
    else:
        try:
            pdf = Pdf(data, flatten=True)
        except ValueError:
            return out  # password-protected
        try:
            if not len(pdf):
                return out
            page = pdf.page(0)
        finally:
            pdf.close()
    lines = page.lines()
    for box, text in lines:
        role = _role(text, fuzzy)
        if role is None:
            continue
        under = _below(lines, box, 26 if role in ("issuer", "recipient") else 16)
        if role == "issuer" and out.issuer is None:
            out.issuer = next((t for t in under if _plausible(t, fuzzy)), None)
        elif role == "issuer_tin" and out.issuer_tin is None:
            out.issuer_tin = next((m.group(1) for t in under if (m := EIN.search(t))), None)
        elif role == "recipient" and out.recipient is None:
            out.recipient = next((t for t in under if _plausible(t, fuzzy)), None)
        elif role == "account" and out.account is None:
            out.account = next((t.strip() for t in under if re.search(r"\d{3}", t)), None)
    if out.issuer is None:
        out.issuer = (_scan_letterhead if fuzzy else _letterhead)(page, lines)
    if out.issuer_tin is None and (m := EIN_LABELLED.search(page.text)):
        out.issuer_tin = m.group(1)
    if out.account is None and (m := ACCOUNT_IN_TEXT.search(page.text)):
        out.account = m.group(1).strip()
    if out.recipient is None and (m := RECIPIENT_IN_TEXT.search(page.text)):
        out.recipient = (m.group(1) or m.group(2)).strip()
    if m := SSN.search(page.text):
        out.recipient_last4 = m.group(1)
    if out.issuer:
        out.issuer = clean_name(out.issuer)
    if out.recipient:
        out.recipient = clean_name(out.recipient)
    return out
