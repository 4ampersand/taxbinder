"""What a Form 1040 says: filing status, who's on it, the state from the address,
the payers and entities it names, the income it only totals, and what it shows was paid
(estimated tax, property tax, gifts; whether, never how much).

Read from the printed text by the form's own captions, never guessed: what isn't there stays
empty. Only the last 4 of an SSN are kept. Nothing read is logged.
"""

from __future__ import annotations

import re

from taxbinder.models.document import Listed, ReturnPerson, ReturnRead

STATUSES = [
    ("married_filing_jointly", r"married filing jointly"),
    ("married_filing_separately", r"married filing separately"),
    ("head_of_household", r"head of household"),
    ("qualifying_surviving_spouse", r"qualifying (?:surviving spouse|widow)"),
    ("single", r"single"),
]
TICK = r"(?:\[\s*[xX✓✔]\s*\]|[☒☑■✔✓]|(?<![A-Za-z])[xX](?![A-Za-z]))"
SSN = re.compile(r"\b\d{3}-?\d{2}-?(\d{4})\b")
EIN = re.compile(r"\b(\d{2}-\d{7})\b")
AMOUNT = re.compile(r"\(?\$?(\d[\d,]*)(?:\.(\d{2})?)?\)?\s*$")  # "1,819." at a line's end
CAPTION = re.compile(r"\b(?:name|number|security|ssn|spouse|initial|relationship)\b", re.I)
LEADER = re.compile(r"(?:\s*\.){2,}")  # dot leaders: "Taxable interest . . . . 2,168."
# income the return only totals, by its line: (kind, line caption)
TOTALS = [
    ("wages", r"^1a\b.*\bw-2\b"),
    ("interest", r"^2b\s+taxable interest"),
    ("dividends", r"^3b\s+ordinary dividends"),
    ("retirement", r"^(?:4[ab]\s+ira distributions|5[ab]\s+pensions and annuities)"),
    ("social_security", r"^6[ab]\s+social security benefits"),
    ("mortgage_interest", r"^8a\s+home mortgage interest"),
]
# what it shows was paid, by its line (1040 line 26; Schedule A lines 5b, 5c, 11 and 12)
PAID = [
    ("estimated", r"^26\b.*estimated tax payments"),
    ("property_tax", r"^5b\b.*real estate taxes"),
    ("vehicle", r"^5c\b.*personal property taxes"),
    ("gifts", r"^1[12]\b.*(?:gifts by cash|other than by cash)"),
]


def _amount(line: str) -> int:
    """The whole dollars at the end of a line; 0 when there's none."""
    m = AMOUNT.search(LEADER.sub(" ", line))
    return int(m.group(1).replace(",", "")) if m else 0


def _name(line: str) -> str:
    """The words before the SSN (or the end): 'Avery Quill 900-00-0101' → 'Avery Quill'."""
    head = SSN.split(line)[0]
    return " ".join(w for w in head.split() if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w))


def _status(text: str) -> str | None:
    at = text.lower().find("filing status")
    if at < 0:
        return None
    area = text[at : at + 400]
    for status, label in STATUSES:
        if re.search(rf"{TICK}\s*{label}", area, re.IGNORECASE):
            return status
    return None


def _people(lines: list[str]) -> list[ReturnPerson]:
    people: list[ReturnPerson] = []

    def add(line: str, role: str) -> None:
        name = _name(line)
        if len(name.split()) >= 2 and not CAPTION.search(name):
            m = SSN.search(line)
            people.append(ReturnPerson(name=name, role=role, last4=m.group(1) if m else None))

    for i, line in enumerate(lines[:-1]):
        low = line.lower()
        if low.startswith("your first name"):
            add(lines[i + 1], "taxpayer")
        elif "spouse's first name" in low:
            add(lines[i + 1], "spouse")
        elif low.startswith("dependents"):
            for row in lines[i + 1 : i + 9]:
                if not SSN.search(row):
                    break  # the dependents table ends where SSNs stop
                add(row, "dependent")
    return people


def _state(lines: list[str]) -> str | None:
    from taxbinder.returns import STATES

    for line in lines:
        if re.search(r"city, town|post office", line, re.IGNORECASE):
            m = re.search(r"\b[Ss]tate\s+([A-Z]{2})\b", line) or re.search(
                r",\s*([A-Z]{2})\s+\d{5}\b", line
            )
            if m and m.group(1) in STATES:
                return m.group(1)
    return None


def _payer(line: str) -> str | None:
    """'Tamarack Savings Bank 1,819.' → 'Tamarack Savings Bank'; None for anything else."""
    line = LEADER.sub(" ", line).strip()
    if not AMOUNT.search(line) or not _amount(line):
        return None
    name = AMOUNT.sub("", line).strip()
    if not re.search(r"[A-Za-z]{2}", name) or re.match(r"^\d+[a-z]?\s", name):
        return None  # a numbered line ("2 Add the amounts on line 1"), not a payer
    return name


def _schedule_b(lines: list[str]) -> list[Listed]:
    out: list[Listed] = []
    part = None
    for line in lines:
        low = line.lower()
        if low.startswith("schedule b"):
            part = "start"
        elif part and re.match(r"^schedule [a-z]\b|^form \d", low) and "schedule b" not in low:
            part = None
        elif part and re.match(r"^part i\s+interest", low):
            part = "interest"
        elif part and re.match(r"^part ii\s+ordinary dividends", low):
            part = "dividends"
        elif part and re.match(r"^part iii\b", low):
            part = None
        elif part in ("interest", "dividends") and re.match(r"^\d+[a-z]?\s", low):
            continue  # its own lines ("1 List name of payer", "2 Add …")
        elif part in ("interest", "dividends") and (name := _payer(line)):
            # a dividend payer's row is a consolidated 1099; it takes a 1099-DIV too (match.py)
            doc_type = "1099_int" if part == "interest" else "1099_composite"
            out.append(Listed(name=name, doc_type=doc_type))
    return out


def _schedule_e(lines: list[str]) -> list[Listed]:
    """Part II (partnerships, S corporations) and Part III (estates and trusts) rows, including
    a continuation statement ("Statement 1 – Schedule E, Part II, Line 28 (continued)")."""
    out: list[Listed] = []
    part = None
    for line in lines:
        low = line.lower()
        if re.match(r"^part ii\s+income or loss from partnerships", low) or (
            low.startswith("statement") and re.search(r"part ii\b|line 28", low)
        ):
            part = "ii"
        elif re.match(r"^part iii\s+income or loss from estates", low) or (
            low.startswith("statement") and re.search(r"part iii\b|line 33", low)
        ):
            part = "iii"
        elif re.match(r"^part iv\b|^part v\b|^schedule [a-z]\b|^form \d", low):
            part = None
        elif part == "ii" and (m := re.match(rf"^[A-Z]\s+(.+?)\s+([PS])\s+{EIN.pattern}", line)):
            doc_type = "k1_1065" if m.group(2) == "P" else "k1_1120s"
            out.append(Listed(name=m.group(1).strip(), ein=m.group(3), doc_type=doc_type))
        elif part == "iii" and (m := re.match(rf"^[A-Z]\s+(.+?)\s+{EIN.pattern}", line)):
            out.append(Listed(name=m.group(1).strip(), ein=m.group(2), doc_type="k1_1041"))
    return out


def _merge(listed: list[Listed]) -> list[Listed]:
    """One per payer: a payer of interest and dividends sends a consolidated 1099."""
    from taxbinder.institutions import name_key

    out: dict[str, Listed] = {}
    for item in listed:
        key = name_key(item.name)
        if key in out and out[key].doc_type != item.doc_type:
            out[key] = out[key].model_copy(update={"doc_type": "1099_composite"})
        out.setdefault(key, item)
    return list(out.values())


def read_1040(text: str) -> ReturnRead:
    """What the return's text says; empty fields for what it doesn't."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    payers = _schedule_b(lines)
    named = {"interest" if p.doc_type == "1099_int" else "dividends" for p in payers}
    totals = [
        kind
        for kind, caption in TOTALS
        if kind not in named  # Schedule B names the payers
        and any(re.search(caption, ln, re.IGNORECASE) and _amount(ln) for ln in lines)
    ]
    listed = _merge(payers + _schedule_e(lines))
    return ReturnRead(
        filing_status=_status(text),
        people=_people(lines),
        state=_state(lines),
        listed=listed,
        totals_only=totals,
        paid=[k for k, c in PAID if any(re.search(c, ln, re.I) and _amount(ln) for ln in lines)],
    )
