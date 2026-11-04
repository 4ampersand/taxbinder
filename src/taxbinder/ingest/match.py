"""Match a classified document to an expectation (§6.2 Match, §5.2a).

Scores (in points) candidates among the tax year's expectations of the same doc type:
- doc type (always, it's the filter): 30
- issuer is the source's institution (or linked to it by lineage): 30. A source name that picks
  out exactly one candidate also counts, because K-1s name the fund, not the platform that sold
  it (on K-1s, or when the issuer wasn't read: a 1099-INT's "Interest" doesn't pick another
  issuer's "Interest" row). Names match in full or by their keys (`names.name_keys`: "Aster",
  "HOP").
- a distinguishing signal, the strongest of:
  account number matches any identifier of the source (primary, legacy, alternate, reference): 40
  the source's name (or a key of it) appears in the text or filename: 30
  the only such expectation at that institution: 30
Auto-link when the best score is ≥ 90 and no rival is within 10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from taxbinder.ingest.classify import Classified
from taxbinder.ingest.names import GENERIC, filename_words, has_word, key_found, name_keys
from taxbinder.models import Expectation, Institution, Source
from taxbinder.slack import digits

AUTO_LINK = 90
# A dividend payer's row takes either form: a consolidated 1099 or a 1099-DIV on its own; an
# interest payer's row takes a consolidated 1099 too (a broker's cash interest, say).
DIVIDENDS = {"1099_composite", "1099_div"}
MARGIN = 10
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
SERIES = re.compile(r"(?i:\bfund)[\s_-]+([IVX]{1,4}|\d{1,2})(?![A-Za-z0-9])")


def fund_numbers(text: str) -> set[int]:
    """'Value-Add Fund II LP' → {2}; 'Fund 3' → {3}. Roman numerals count only in capitals."""
    out = set()
    for m in SERIES.finditer(text):
        n = m.group(1)
        if n.isdigit():
            out.add(int(n))
        elif n in ROMAN:
            out.add(ROMAN[n])
    return out


@dataclass(frozen=True)
class Candidate:
    expectation: Expectation
    source: Source
    score: int


@dataclass(frozen=True)
class MatchResult:
    best: Candidate | None  # set only when it's safe to link automatically
    candidates: list[Candidate]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _related(institution: str, institutions: dict[str, Institution]) -> set[str]:
    """The institution plus any it is linked to by lineage, in either direction."""
    related = {institution}
    for inst in institutions.values():
        for link in inst.lineage:
            if inst.id == institution or link.institution == institution:
                related |= {inst.id, link.institution}
    return related


def same_form(row: str | None, doc: str | None) -> bool:
    return row == doc or {row, doc} <= DIVIDENDS or (row, doc) == ("1099_int", "1099_composite")


def match(
    c: Classified,
    text: str,
    filename: str,
    expectations: list[Expectation],
    sources: list[Source],
    institutions: list[Institution],
    people: list[str] = (),  # taxpayers' names: a row named after a person isn't a fund name
) -> MatchResult:
    if c.doc_type is None or c.tax_year is None:
        return MatchResult(None, [])
    by_id = {s.id: s for s in sources}
    insts = {i.id: i for i in institutions}
    issuers = _related(c.issuer, insts) if c.issuer else set()
    file_flat = _norm(filename)
    file_lower, text_lower = filename_words(filename).lower(), text.lower()
    text_words = " ".join(re.findall(r"[a-z0-9]+", text_lower))

    def named(s: Source) -> int:
        """2: the full name, or a key of it ("Aster", "HOP"), appears; 1: only the sponsor's own
        name does ("tidemark-LOCPledged" by "tidemark"), which doesn't tell the sponsor's rows
        apart; 0: neither.

        In the text the full name must appear as whole words ("Qualified Opportunity Fund
        investments" is not "Opportunity Fund I"); in a file name it may be squashed together
        (`MRBaysideCommons`), as people write them.
        """
        personal = {w for p in people for w in re.findall(r"[a-z]+", p.lower())}
        sponsor = insts.get(s.institution)
        own = {
            _norm(n)
            for n in ([sponsor.name, *sponsor.aliases] if sponsor else []) + [s.institution]
        }
        for name in {s.name, re.sub(r"\(.*?\)", " ", s.name)}:  # with and without "(Easton)"
            full = _norm(name)
            words = " ".join(re.findall(r"[a-z0-9]+", name.lower()))
            a_person = set(words.split()) <= personal  # "Rowan": in the text, it's who, not what
            if len(full) >= 4 and (
                full in file_flat or (not a_person and has_word(words, text_words))
            ):
                return 2
            # without its account digits ("Keystep 2255" in "Tidemark-Keystep-5521.pdf")
            letters = [  # neither generic words nor the sponsor's own name ("tidemark-Trust")
                w for w in re.findall(r"[a-z]+", name.lower()) if w not in GENERIC and w not in own
            ]
            bare = "".join(letters)
            if len(bare) >= 6 and not a_person and bare in file_flat:
                return 2
        found = [
            k
            for k in name_keys(s.name)
            if key_found(k, file_lower, "" if k in personal else text_lower)
        ]
        if any(_norm(k) not in own for k in found):
            return 2
        return 1 if found else 0

    # Account digits in the file name ("BrackleyWealth-…-4417.pdf"): the last four of a source's
    # account numbers, or of its id (ids keep an account's last four), end a number in the name.
    # Groups split by dashes are joined ("123-456789" → "123456789"); each run counts too. Never
    # across spaces: "K1 2024" is a form and a year, not "12024".
    groups = re.findall(r"\d[\d-]*\d", filename)
    file_numbers = [
        n for g in groups for n in ("".join(re.findall(r"\d+", g)), *re.findall(r"\d+", g))
    ]
    file_numbers = [  # four digits count unless they look like a year ("LOCP-5503", not "2023")
        n for n in file_numbers if len(n) >= 5 or (len(n) == 4 and not 1990 <= int(n) <= 2039)
    ]

    def last4(s: Source) -> set[str]:
        out = {re.sub(r"\D", "", i.number)[-4:] for i in s.identifiers if i.number}
        out |= set(re.findall(r"(?<!\d)(\d{4})(?!\d)", s.id))
        return {x for x in out if len(x) == 4}

    def account_in_name(s: Source) -> bool:
        return any(n.endswith(d) for n in file_numbers for d in last4(s))

    pool = [
        e
        for e in expectations
        if e.year == c.tax_year and same_form(e.doc_type, c.doc_type) and e.source in by_id
    ]
    level = {e.id: named(by_id[e.source]) for e in pool}
    # rows named specifically win over rows only named by their sponsor's word
    named_ids = {i for i, lv in level.items() if lv == 2} or {i for i, lv in level.items() if lv}
    # Same sponsor, several funds ("… Fund II" and "… Fund III"): the fund's number decides.
    doc_numbers = fund_numbers(filename_words(filename)) | fund_numbers(text)
    same_institution: dict[str, int] = {}
    for e in pool:
        inst = by_id[e.source].institution
        same_institution[inst] = same_institution.get(inst, 0) + 1

    by_account = {  # an account ending only counts for the sponsor the document is from, if known
        e.id
        for e in pool
        if account_in_name(by_id[e.source])
        and (not issuers or by_id[e.source].institution in issuers)
    }
    candidates = []
    for e in pool:
        s = by_id[e.source]
        is_named = e.id in named_ids
        score = 30
        # A name that picks out one row also tells us the sponsor (K-1s name the fund only).
        # Not on other forms from a known issuer: "Interest" is printed on every 1099-INT.
        sponsor_by_name = not issuers or c.doc_type.startswith("k1")
        if (
            s.institution in issuers
            or (is_named and len(named_ids) == 1 and sponsor_by_name)
            or (e.id in by_account and len(by_account) == 1)  # an account number picks one row
        ):
            score += 30
        signals = [0]
        printed = digits(c.account_number) if c.scanned and c.account_number else c.account_number
        if printed and any(i.matches(printed) for i in s.identifiers):
            signals.append(40)
        if e.id in by_account:
            signals.append(40)
        if is_named:
            signals.append(30)
        if s.institution in issuers and same_institution[s.institution] == 1:
            signals.append(30)
        score += max(signals)
        if is_named and doc_numbers and (row_numbers := fund_numbers(s.name)):
            score += 10 if row_numbers & doc_numbers else -20
        candidates.append(Candidate(e, s, score))
    candidates.sort(key=lambda x: x.score, reverse=True)

    best = candidates[0] if candidates else None
    if best and (
        best.score < AUTO_LINK
        or (len(candidates) > 1 and best.score - candidates[1].score < MARGIN)
    ):
        best = None
    return MatchResult(best, candidates)
