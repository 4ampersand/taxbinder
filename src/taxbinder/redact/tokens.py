"""Vault-wide placeholders (§9.3): the same real value always gets the same token, in every
document and year. The map is a vault record (`registry/pseudonyms.yaml`, values encrypted)."""

from __future__ import annotations

import re
from collections import Counter

from taxbinder.models.privacy import Placeholder, PseudonymMap
from taxbinder.store import RecordNotFound, RecordStore

# Token prefixes for values compared digit by digit (separators and case don't matter).
NUMERIC = {"TIN", "EIN", "ACCT", "NUM", "PHONE", "ZIP"}
TOKEN = re.compile(r"\[?\b([A-Z]+_\d{2,})\b\]?")


def canon(prefix: str, value: str) -> str:
    """The comparison form of a value: digits and letters only for numbers, else words."""
    if prefix in NUMERIC:
        return re.sub(r"[^0-9A-Za-z*]", "", value).upper()
    return " ".join(re.sub(r"[^\w@.'-]+", " ", value).split()).casefold()


class Pseudonyms:
    def __init__(self, store: RecordStore) -> None:
        try:
            self._map, self._rev = store.get("pseudonyms", "pseudonyms")
        except RecordNotFound:
            self._map, self._rev = PseudonymMap(), None
        self.entries: list[Placeholder] = list(self._map.entries)
        self._by_key = {}
        for e in self.entries:
            prefix = e.token.rsplit("_", 1)[0]
            self._by_key[(prefix, canon(prefix, e.secret))] = e.token
        self._next: Counter[str] = Counter()
        for e in self.entries:
            prefix, n = e.token.rsplit("_", 1)
            self._next[prefix] = max(self._next[prefix], int(n))
        self.added = 0

    def token(self, prefix: str, value: str, also: list[str] | tuple[str, ...] = ()) -> str:
        """The token for `value`; `also` are other forms of the same thing (an account's old
        numbers) that must share it."""
        pairs = [((prefix, canon(prefix, v)), v) for v in (value, *also) if canon(prefix, v)]
        token = next((self._by_key[k] for k, _ in pairs if k in self._by_key), None)
        if token is None:
            self._next[prefix] += 1
            token = f"{prefix}_{self._next[prefix]:02d}"
        for k, v in pairs:
            if k not in self._by_key:
                self._by_key[k] = token
                self.entries.append(Placeholder(token=token, secret=" ".join(v.split())))
                self.added += 1
        return token

    def save(self, store: RecordStore, actor: str) -> None:
        if not self.added:
            return
        with store.operation(actor, f"redaction: {self.added} new placeholder(s)"):
            self._rev = store.put(self._map.model_copy(update={"entries": self.entries}), self._rev)
        self.added = 0

    def display(self) -> dict[str, str]:
        """token → the first real value recorded for it (for translating answers back)."""
        out: dict[str, str] = {}
        for e in self.entries:
            out.setdefault(e.token, e.secret)
        return out


def unredact(text: str, store: RecordStore) -> str:
    """Put the real values back into a reply that uses placeholders (`TIN_01`, `[ACCT_07]`)."""
    shown = Pseudonyms(store).display()
    return TOKEN.sub(lambda m: shown.get(m.group(1), m.group(0)), text)
