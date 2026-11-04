"""Rule-based classification (§6.2 Classify ① and ②): doc type, tax year, issuer, account.

No LLM in Phase 1. Works on the first pages' text plus the filename.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from taxbinder.extract.parties import read_parties
from taxbinder.ingest.names import filename_words, has_word, name_keys, strip_boilerplate
from taxbinder.institutions import find
from taxbinder.models import DocTypeDef, Institution
from taxbinder.models.common import DocType
from taxbinder.models.document import ReturnInfo
from taxbinder.pdfdoc import Page
from taxbinder.returns import recognise

# Most specific first. Patterns are matched case-insensitively against text, then filename.
DOC_PATTERNS: list[tuple[DocType, str]] = [
    ("k1_1120s", r"schedule k-1\s*\(form 1120-?s\)"),
    ("k1_1041", r"schedule k-1\s*\(form 1041\)"),
    ("k1_state", r"\b592-b\b|schedule k-1\s*\(568\)"),
    ("k1_1065", r"schedule k-1\s*\(form 1065\)|\bk-?1\b"),
    (
        "1099_composite",
        r"form 1099-b\b|consolidated (tax|1099)|composite (form|statement)|\b1099 composite"
        r"|tax reporting statement",
    ),
    ("5498_sa", r"\b5498-sa\b"),
    ("5498", r"\b5498\b(?!-)"),  # IRA; not 5498-SA / 5498-ESA
    ("1099_int", r"form 1099-int\b|\b1099-int\b"),
    ("1099_div", r"form 1099-div\b|\b1099-div\b"),
    ("1099_q", r"form 1099-q\b|\b1099-q\b"),
    ("1099_r", r"form 1099-r\b|\b1099-r\b"),
    ("1099_nec", r"form 1099-nec\b"),
    ("1099_misc", r"form 1099-misc\b"),
    ("ssa_1099", r"\bssa-1099\b"),
    ("1098", r"form 1098\b|mortgage interest statement|\b1098-mort\b"),
    ("w2", r"form w-2\b|wage and tax statement|\bw-?2\b"),
    ("fbar_filing", r"fincen\s*(form\s*)?114|bsa e-filing"),
    ("payment_confirmation", r"\beftps\b|payment confirmation|web pay confirmation"),
    (  # FTB Web Pay / MyFTB receipt: a payment date plus Web Pay or the FTB's name; FTB notices
        # that only mention Web Pay as a way to pay have no payment date
        "payment_confirmation",
        r"(?s)\A(?=.*\bpayment date\b)(?=.*(?:\bweb pay\b|franchise tax board))",
    ),
    ("property_tax_bill", r"secured property tax|property tax bill"),
    ("vehicle_registration", r"vehicle license fee|registration renewal"),
    ("account_change_notice", r"account number (is |will be )?chang"),
    (
        "donation_ack",
        r"thank you for your (generous )?(gift|donation|contribution)|no goods or services",
    ),
]
YEAR_PATTERNS = [
    r"\b(?:calendar|tax) year\s+(20\d{2})\b",
    r"\b(20\d{2})\s+(?:schedule k-1|form\s+[\w-]+)",
    r"(?:schedule k-1|form\s+[\w-]+)\s*(?:\([^)]*\))?\s+(20\d{2})\b",
]
# A fiscal-year K-1: filed under the year its tax year ends. Both dates filled in; the
# blank "tax year beginning ____ ending ____" is on every calendar-year K-1.
FISCAL = re.compile(
    r"tax year beginning\s+\d{1,2}/\d{1,2}/(?:20)?\d{2}\s*,?\s*(?:and\s+)?ending\s+"
    r"(\d{1,2})/(\d{1,2})/((?:20)?\d{2})\b",
    re.IGNORECASE,
)
ACCOUNT = re.compile(
    r"(?i)\b(?:account|acct)\s*(?:number|no\.?|#)?\s*[:#]?\s*([x*•…\d][\dx*•…\- ]{3,22}\d)"
)


@dataclass
class Classified:
    doc_type: DocType | None
    tax_year: int | None
    issuer: str | None
    account_number: str | None
    is_corrected: bool
    confidence: float
    method: str
    tax_return: ReturnInfo | None = None
    issuer_name: str | None = None  # as read from the form (cleaned up)
    issuer_tin: str | None = None
    recipient_name: str | None = None  # who it's for, as printed
    recipient_last4: str | None = None
    scanned: bool = False  # read from a scan or photo: compared with look-alike slack
    fiscal_year_end: date | None = None  # a fiscal-year K-1: filed by the year this is in


def _fiscal_end(text: str) -> date | None:
    """Where a fiscal-year K-1's tax year ends; None for a calendar year or a blank line."""
    if not (m := FISCAL.search(text)):
        return None
    month, day, year = (int(g) for g in m.groups())
    try:
        end = date(year if year > 99 else 2000 + year, month, day)
    except ValueError:
        return None
    return None if (end.month, end.day) == (12, 31) else end


def _doc_type(
    text: str, filename: str, custom: Sequence[DocTypeDef] = ()
) -> tuple[DocType | None, str | None]:
    """The form named **earliest** in the text wins: a document's own title is at the top, and a
    1099 that mentions "K-1" (or "Form 1099-DIV") in its body is still what its title says. The
    person's added types (their identifying words) take part the same way and win a tie."""
    mine = [
        (d.id, rf"(?<!\w){re.escape(k)}(?!\w)") for d in custom for k in d.keywords if k.strip()
    ]
    rules = [(-1, t, p) for t, p in mine] + [(i, t, p) for i, (t, p) in enumerate(DOC_PATTERNS)]
    for where, haystack in (("rules", text), ("filename", filename.replace("_", " "))):
        hits = [
            (m.start(), rank, doc_type)
            for rank, doc_type, pattern in rules
            if (m := re.search(pattern, haystack, re.IGNORECASE))
        ]
        if hits:
            return min(hits)[2], where
    return None, None


def _named_before(text: str, at: int, custom: Sequence[DocTypeDef]) -> bool:
    """Another form is named in the text before a return's title."""
    doc_type, _ = _doc_type(text[:at], "", custom)
    return doc_type is not None


def _tax_year(text: str, filename: str, latest: int) -> int | None:
    for pattern in YEAR_PATTERNS:
        if m := re.search(pattern, text, re.IGNORECASE):
            year = int(m.group(1))
            if 2000 <= year <= latest:
                return year
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", f"{filename} {text}") if int(y) <= latest]
    return Counter(years).most_common(1)[0][0] if years else None


def _names(inst: Institution) -> list[str]:
    return [inst.name, *inst.aliases, inst.id.replace("-", " ")]


def _issuer(text: str, filename: str, institutions: list[Institution]) -> str | None:
    """Institution whose name or alias appears; the longest match wins.

    IRS form boilerplate is ignored (it is on every federal form). An institution's short keys
    ("Corbel", "CFC") count only in the file name, where people write the sponsor.
    """
    name_part = filename_words(filename).lower()
    haystack = f"{name_part}\n{strip_boilerplate(text)}".lower()
    best: tuple[int, str] | None = None
    for inst in institutions:
        for name in _names(inst):
            n = name.strip().lower()
            if len(n) >= 3 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", haystack):
                if best is None or len(n) > best[0]:
                    best = (len(n), inst.id)
        for key in name_keys(inst.name):
            if has_word(key, name_part) and (best is None or len(key) > best[0]):
                best = (len(key), inst.id)
    return best[1] if best else None


def classify(
    text: str,
    filename: str,
    institutions: list[Institution],
    latest_year: int,
    custom_types: Sequence[DocTypeDef] = (),
    data: bytes | None = None,
    pages: list[Page] | None = None,
) -> Classified:
    """What a document is. With the PDF's bytes, the issuer and account are read by their place
    on the form; a read name finds its institution however it's spelled. `pages`: what
    was recognised on a scan or photo, whose text is `text`."""
    found = recognise(text, latest_year)
    if found is not None and _named_before(text, found.at, custom_types):
        found = None  # e.g. a K-1's cover letter that mentions "your … Income Tax Return"
    if found is not None:
        year = found.tax_year or _tax_year(text, filename, latest_year)
        confidence = 0.75 + (0.25 if year else 0)  # a return's issuer is its filer
        return Classified(found.doc_type, year, None, None, False, confidence, "rules", found.info)
    doc_type, found_in = _doc_type(text, filename, custom_types)
    tax_year = _tax_year(text, filename, latest_year)
    fiscal = _fiscal_end(text) if doc_type and doc_type.startswith("k1_") else None
    if fiscal is not None and 2000 <= fiscal.year <= latest_year:
        tax_year = fiscal.year
    parties = read_parties(data, doc_type, pages) if (data or pages) and text else None
    issuer = None
    if parties and parties.issuer:
        issuer = find(parties.issuer, parties.issuer_tin, institutions, scanned=bool(pages))
    issuer = issuer or _issuer(text, filename, institutions)
    account = parties.account if parties and parties.account else None
    if account is None and (m := ACCOUNT.search(text)):
        account = m.group(1).strip()
    corrected = bool(re.search(r"\bCORRECTED\b|\bAMENDED\b", text))
    confidence = (
        (0.5 if found_in == "rules" else 0.3 if found_in else 0)
        + (0.25 if tax_year else 0)
        + (0.25 if issuer or (parties and parties.issuer) else 0)
    )
    if pages or fiscal:
        confidence = min(confidence, 0.75)  # a scan, or a fiscal year: always looked at
    method = "+".join(sorted({"rules" if text else "filename", found_in or "rules"}))
    return Classified(
        doc_type,
        tax_year,
        issuer,
        account,
        corrected,
        round(confidence, 2),
        method,
        issuer_name=parties.issuer if parties else None,
        issuer_tin=parties.issuer_tin if parties else None,
        recipient_name=parties.recipient if parties else None,
        recipient_last4=parties.recipient_last4 if parties else None,
        scanned=bool(pages),
        fiscal_year_end=fiscal,
    )
