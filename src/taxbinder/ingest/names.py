"""Name clues for classification and matching: file-name words, distinctive keys, IRS boilerplate.

K-1s name the partnership entity, not the sponsor, and every federal form carries IRS text. People
name files with short forms (`MRAster`, `CFC_Pikeway_Fund`). So a name is reduced to keys: its
ALL-CAPS abbreviation in parentheses ("HOP") and its first distinctive word ("Aster"), matched as
whole words. Short keys (under 5 letters) only count in the file name, where they were put
on purpose.
"""

from __future__ import annotations

import re

# Words that don't tell funds apart, including K-1 boilerplate ("Qualified Opportunity Fund").
GENERIC = {
    *"a an and at of the for in on to".split(),
    *"llc lp llp inc corp co ltd trust".split(),
    *"fund funds partners partner partnership inv investor investors".split(),
    *"investment investments".split(),
    *"capital group holdings properties property apartments apartment companies company".split(),
    *"insurance value added growth opportunity qualified shares income".split(),
    *"north south east west place center living net lease".split(),
    # words that describe the document, not who sent it
    *"tax statement consolidated interest dividend dividends account savings".split(),
    *"withdrawal contribution distribution benefit verification letter payslip".split(),
}
TEXT_KEY_MIN = 5  # shorter keys only count in the file name

# IRS text printed on every federal form; it never says who issued the form.
IRS_BOILERPLATE = re.compile(
    r"(?:www\.)?irs\.gov\S*|internal revenue service|department of the treasury"
    r"|\birs center\b|\bsee irs\b|\bfor irs use only\b",
    re.IGNORECASE,
)


def filename_words(filename: str) -> str:
    """`MRAster_2025.pdf` → `MR Aurora 2025 pdf`: camel case and separators become spaces."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", filename)
    return re.sub(r"[_\-.]+", " ", s)


def strip_boilerplate(text: str) -> str:
    return IRS_BOILERPLATE.sub(" ", text)


def name_keys(name: str) -> list[str]:
    """`Hawksmoor Office Park(HOP)` → [btc, hawksmoor]; `Opportunity Fund I` → []."""
    abbreviations = [a.lower() for a in re.findall(r"\(\s*([A-Z]{2,})\s*\)", name)]
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", " ", name.lower()))
        if w not in GENERIC and len(w) >= 3 and not (w.isdigit() and len(w) != 3)
    ]
    return [*abbreviations, *words[:1]]


def has_word(key: str, haystack: str) -> bool:
    """`key` as a whole word in an already-lowercased haystack."""
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack) is not None


def key_found(key: str, filename_lower: str, text_lower: str) -> bool:
    if has_word(key, filename_lower):
        return True
    return len(key) >= TEXT_KEY_MIN and has_word(key, text_lower)
