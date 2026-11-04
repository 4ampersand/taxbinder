"""Record ids from names: slugs for institutions and sources, and the suffix of a checklist row."""

from __future__ import annotations

import re

EXPECTATION_SUFFIX = {"k1_1065": "k1", "1099_composite": "1099"}  # others: doc type, dashed


def slugify(text: str) -> str:
    """Ids become file names (not encrypted): digit runs of 5+ (account numbers) keep the last 4."""
    text = re.sub(r"\d{5,}", lambda m: m.group()[-4:], text)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def institution_slug(name: str) -> str:
    """Parenthetical abbreviations don't belong in ids: 'Copperfield Collective (CFC)'."""
    return slugify(re.sub(r"\(.*?\)", "", name))
