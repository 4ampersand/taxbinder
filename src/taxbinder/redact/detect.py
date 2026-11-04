"""What to hide in a text (§9.2): values the vault knows, then tax-document patterns.

Known values come first and win: every TIN, EIN, account number (current and old numbers of a
Source share one placeholder), name and address the vault holds, in the forms they're printed
in (with or without dashes, masked to the last 4, middle initials, street abbreviations).
Patterns catch the rest: SSNs, EINs, account numbers next to "account", street addresses,
ZIP codes after a state, email, phone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from taxbinder.models.privacy import PrivateValue
from taxbinder.redact.tokens import Pseudonyms
from taxbinder.store import RecordStore

B, E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"  # whole-token boundaries
SEP = r"[\s.\-/]?"  # optional separator inside a printed number
# XXX-XX-, ****, …: atomic, so a long run of X's with no digits after it fails at once instead of
# trying every way to split the run (catastrophic backtracking: minutes on a K-1's filler lines)
MASK = r"(?>(?:[Xx*•#…]+[\s.\-]?)+)"
GAP = r"[\s,]+"

STREET_WORDS = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "court": "ct",
    "drive": "dr",
    "lane": "ln",
    "boulevard": "blvd",
    "place": "pl",
    "terrace": "ter",
    "circle": "cir",
    "parkway": "pkwy",
    "highway": "hwy",
    "suite": "ste",
    "apartment": "apt",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "square": "sq",
    "trail": "trl",
    "way": "way",
}
STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR"
).split()


@dataclass(frozen=True)
class Finder:
    regex: re.Pattern[str]
    prefix: str
    token: str | None = None  # known values: decided up front; patterns: from the match
    known: bool = False


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    token: str
    known: bool


def _number(value: str) -> str:
    chars = re.sub(r"[^0-9A-Za-z]", "", value)
    return B + SEP.join(re.escape(c) for c in chars) + E


def _masked(value: str) -> str | None:
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) < 6:
        return None
    last4 = SEP.join(digits[-4:])
    return rf"(?:{B}{MASK}{last4}{E}|ending{GAP}(?:in{GAP})?{last4}{E})"


def _words(words: list[str]) -> str:
    return r"\s+".join(re.escape(w) for w in words)


def _name(value: str) -> list[str]:
    """'Maria Example' also as 'Maria Q. Example', 'Example, Maria'; returns regexes."""
    words = [w for w in re.split(r"[\s,]+", value) if w]
    out = [B + _words(words) + E]
    if len(words) >= 2:
        first, last = words[0], words[-1]
        middle = r"(?:\s+[A-Za-z]\.?|\s+[A-Za-z]+)?"
        out.append(B + re.escape(first) + middle + r"\s+" + re.escape(last) + E)
        initial = r"(?:\s+[A-Za-z]\.?)?"  # 'Example, Jonathan Q.': an initial only, not any word
        out.append(B + re.escape(last) + r",\s*" + re.escape(first) + initial + E)
    return out


def _street(value: str) -> str:
    """The street line of an address ('1234 Elm Court, …'), with abbreviations either way."""
    line = value.split(",")[0]
    parts = []
    for w in line.split():
        bare = w.strip(".").lower()
        alts = {bare} | ({STREET_WORDS[bare]} if bare in STREET_WORDS else set())
        alts |= {k for k, v in STREET_WORDS.items() if v == bare}
        parts.append("(?:" + "|".join(re.escape(a) + r"\.?" for a in sorted(alts)) + ")")
    return B + r"\s+".join(parts) + E


def known_values(
    store: RecordStore, extra: list[PrivateValue] | None = None
) -> Iterator[tuple[str, str, tuple[str, ...]]]:
    """(prefix, value, other forms sharing its placeholder) for everything the vault knows."""
    from taxbinder.privacy import values

    for t in store.list("taxpayer"):
        yield ("PERSON" if t.kind == "individual" else "PARTY"), t.name, ()
        if t.tin:
            yield ("TIN" if t.kind == "individual" else "EIN"), t.tin, ()
    for s in store.list("source"):
        numbers = [i.number for i in s.identifiers if i.number]
        if numbers:
            yield "ACCT", numbers[0], tuple(numbers[1:])
    for d in store.list("document"):
        c = d.classification
        if c and c.account_number:
            yield "ACCT", c.account_number, ()
    for c in store.list("claim"):
        number = getattr(getattr(c, "payment", None), "confirmation_number", None)
        if number:
            yield "NUM", number, ()
    kinds = {
        "name": "PERSON",
        "address": "ADDR",
        "number": "NUM",
        "email": "EMAIL",
        "phone": "PHONE",
        "other": "OTHER",
    }
    for v in [*values(store), *(extra or [])]:
        yield kinds[v.kind], v.secret, ()


# Words in household names that aren't names themselves (never hidden on their own)
NAME_WORDS = {
    "and",
    "the",
    "trust",
    "trustee",
    "family",
    "revocable",
    "irrevocable",
    "living",
    "estate",
    "foundation",
    "llc",
    "inc",
    "corp",
    "company",
    "holdings",
    "partners",
    "fund",
    "jtwros",
    "joint",
    "tenants",
    "dated",
    "household",
    "primary",
    "spouse",
    "taxpayer",
}


def known_finders(store: RecordStore, tokens: Pseudonyms) -> list[Finder]:
    finders: list[Finder] = []
    names: set[str] = set()
    for prefix, value, also in known_values(store):
        token = tokens.token(prefix, value, also)
        regexes: list[str] = []
        for v in (value, *also):
            if prefix in ("TIN", "EIN", "ACCT", "NUM", "PHONE"):
                if len(re.sub(r"[^0-9A-Za-z]", "", v)) >= 4:
                    regexes.append(_number(v))
                if masked := _masked(v):
                    regexes.append(masked)
            elif prefix in ("PERSON", "PARTY"):
                regexes += _name(v)
            elif prefix == "ADDR":
                regexes += [_street(v), B + _words(v.split()) + E]
            else:
                regexes.append(B + _words(v.split()) + E)
        finders += [Finder(re.compile(r, re.IGNORECASE), prefix, token, True) for r in regexes]
        if prefix in ("PERSON", "PARTY"):
            names.update(v for v in (value, *also))
    # Each distinctive part of a household name, on its own too: a surname on a trust's or joint
    # account's title line, or a name split by the page layout, would otherwise stay visible.
    # The full name still wins where it appears whole (known values: longer beats shorter).
    for part in sorted({w for n in names for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", n)}):
        if part.lower() not in NAME_WORDS:
            token = tokens.token("NAME", part)
            finders.append(
                Finder(re.compile(B + re.escape(part) + E, re.IGNORECASE), "NAME", token, True)
            )
    return finders


PATTERNS: list[tuple[str, str]] = [
    # (prefix, regex): the whole match is hidden
    ("TIN", rf"{B}\d{{3}}-\d{{2}}-\d{{4}}{E}"),  # SSN / ITIN
    ("TIN", rf"{B}[Xx*•]{{3}}[\s-]?[Xx*•]{{2}}[\s-]?\d{{4}}{E}"),  # masked SSN
    ("EIN", rf"{B}\d{{2}}-\d{{7}}{E}"),
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("PHONE", rf"{B}(?:\+?1[\s.-]?)?\(?\d{{3}}\)?[\s.-]\d{{3}}[\s.-]\d{{4}}{E}"),
    (
        "ADDR",
        rf"{B}\d{{1,6}}(?:\s+[A-Z][A-Za-z.'-]*){{1,4}}\s+(?:"
        + "|".join(
            sorted(
                {
                    w.capitalize()
                    for k, v in STREET_WORDS.items()
                    for w in (k, v)
                    if k not in ("suite", "apartment", "north", "south", "east", "west")
                }
            )
        )
        + rf")\.?(?:,?\s+(?:Suite|Ste\.?|Apt\.?|Unit|#)\s*[\w-]+)?{E}",
    ),
]
# Groups: (prefix, regex with one group = the part to hide)
CONTEXT: list[tuple[str, str]] = [
    (
        "ACCT",
        r"(?i:\b(?:account|acct|a/c)(?:\s+(?:number|no\.?|#))?\s*[:#]?\s*)([A-Z0-9][A-Z0-9*Xx•…-]{3,}[0-9])",
    ),
    ("ZIP", r"\b(?:" + "|".join(STATES) + r")\s+(\d{5}(?:-\d{4})?)\b"),
]


def pattern_finders() -> list[Finder]:
    return [Finder(re.compile(r), p) for p, r in PATTERNS] + [
        Finder(re.compile(r), p) for p, r in CONTEXT
    ]


def find(text: str, finders: list[Finder], tokens: Pseudonyms) -> list[Span]:
    """Non-overlapping spans to hide: longer beats shorter; known values win a tie."""
    spans: list[Span] = []
    for f in finders:
        for m in f.regex.finditer(text):
            start, end = m.span(1) if m.re.groups else m.span()
            if end - start < 2:
                continue
            token = f.token or tokens.token(f.prefix, text[start:end])
            spans.append(Span(start, end, token, f.known))
    # the longest match wins (a name inside an email address goes with the address); a known value
    # wins a tie, so the same value keeps the same placeholder
    spans.sort(key=lambda s: (-(s.end - s.start), not s.known, s.start))
    chosen: list[Span] = []
    for s in spans:
        if not any(s.start < c.end and c.start < s.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


def apply(text: str, spans: list[Span]) -> str:
    for s in reversed(spans):
        text = text[: s.start] + f"[{s.token}]" + text[s.end :]
    return text
