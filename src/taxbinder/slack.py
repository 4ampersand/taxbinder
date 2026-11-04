"""Reading slips on scans: S/5, O/0, I/1/l and B/8 look alike to text recognition.

Used only to compare what a scan says with values the vault already knows (names, account
endings), never to read amounts: a figure is read as printed or not at all.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_SAME = str.maketrans({"s": "5", "o": "0", "i": "1", "l": "1", "b": "8"})
_DIGIT = str.maketrans({"S": "5", "s": "5", "O": "0", "o": "0", "I": "1", "l": "1", "B": "8"})


def fold(text: str) -> str:
    """Lower case with look-alikes made the same: 'Tamarack 5avings' ≈ 'tamarack savings'."""
    return text.lower().translate(_SAME)


def close(a: str, b: str) -> bool:
    """Two names that differ only by look-alikes or a slip or two ('Tarnarack' ≈ 'Tamarack')."""
    fa, fb = fold(a), fold(b)
    return fa == fb or (min(len(fa), len(fb)) >= 6 and SequenceMatcher(None, fa, fb).ratio() >= 0.9)


def digits(number: str) -> str:
    """An account number read from a scan, with a look-alike letter beside a digit made that
    digit ('…33O4' → '…3304'); letters away from digits stay ('HSA…')."""
    return re.sub(
        r"(?<=\d)[SsOoIlB]|[SsOoIlB](?=\d)", lambda m: m.group().translate(_DIGIT), number
    )
