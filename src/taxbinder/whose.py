"""Whose document it is: the recipient's name decides the person, both spouses, or
the trust named on it. An unclear name is asked on review, never guessed.

A document stays in the vault it was added to: a household's vault holds documents in personal
and trust names alike. When the trust also files from its own vault, a move there is offered
(`entities.move_document`), never made by itself.
"""

from __future__ import annotations

import re

from taxbinder.institutions import name_key
from taxbinder.models import Taxpayer
from taxbinder.models.document import Recipient
from taxbinder.slack import close
from taxbinder.store import RecordStore

TRUST = re.compile(r"\btrust\b|\btrustee\b|\bestate of\b", re.IGNORECASE)


def _words(name: str) -> list[str]:
    """'Avery J. Quill' → ['avery', 'quill']: initials and punctuation aside."""
    return [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 1]


def _person(part: list[str], people: list[Taxpayer], scanned: bool = False) -> str | None:
    """The one person whose first and last names are these (None: nobody, or more than one).
    `scanned`: allowing for reading slips ("Avcry")."""
    if len(part) < 2:
        return None  # a surname alone is shared by the family

    def same(a: str, b: str) -> bool:
        return close(a, b) if scanned else a == b

    found = [
        p.id
        for p in people
        if (w := _words(p.name)) and len(w) >= 2 and same(w[0], part[0]) and same(w[-1], part[-1])
    ]
    return found[0] if len(found) == 1 else None


def recipient_of(
    printed: str | None, last4: str | None, taxpayers: list[Taxpayer], scanned: bool = False
) -> Recipient:
    """'Avery Quill & Jordan Quill' → both; 'Quill Family Trust' → the trust, if the vault knows
    it; anything unclear → asked. A last 4 that isn't the person's own makes it unclear too."""
    if not printed:
        return Recipient()
    if TRUST.search(printed):
        key = name_key(printed)
        trusts = [
            t.id
            for t in taxpayers
            if t.kind != "individual" and (close if scanned else str.__eq__)(name_key(t.name), key)
        ]
        return Recipient(printed=printed, taxpayers=trusts[:1], ask=not trusts)
    people = [t for t in taxpayers if t.kind == "individual"]
    parts = [_words(p) for p in re.split(r"\s*(?:&|\band\b|/|;)\s*", printed, flags=re.I)]
    parts = [p for p in parts if p]
    if not parts:
        return Recipient(printed=printed, ask=True)
    surname = parts[-1][-1] if len(parts[-1]) >= 2 else None
    found = []
    for part in parts:  # "Avery and Jordan Quill": Avery shares the last part's surname
        if len(part) == 1 and surname and len(parts) > 1:
            part = [part[0], surname]
        found.append(_person(part, people, scanned))
    if None in found:
        return Recipient(printed=printed, ask=True)
    ids = list(dict.fromkeys(f for f in found if f))
    if len(ids) == 1 and last4:
        person = next(p for p in people if p.id == ids[0])
        tin = person.last4 or re.sub(r"\D", "", person.tin or "")[-4:]
        if tin and tin != last4:
            return Recipient(printed=printed, taxpayers=ids, ask=True)  # not their number
    return Recipient(printed=printed, taxpayers=ids)


CHANGED = "whose-changed"  # the person changed whose it was (counted in the diagnostic info)


def label(recipient: Recipient | None, taxpayers: dict[str, Taxpayer]) -> str:
    """'Avery Quill', 'Avery and Jordan Quill', 'Quill Family Trust'; '' when unknown."""
    if recipient is None or not recipient.taxpayers:
        return ""
    names = [taxpayers[t].name if t in taxpayers else t for t in recipient.taxpayers]
    return " and ".join(names)


def set_whose(
    store: RecordStore, doc: str, taxpayers: list[str], actor: str, new_trust: bool = False
) -> None:
    """A person says whose a document is. `new_trust`: the trust printed on it, not yet known,
    becomes one of the vault's taxpayers (so the next document in its name is known)."""
    from taxbinder.slugs import slugify

    document, rev = store.get("document", doc)
    current = document.recipient or Recipient()
    known = {t.id: t for t in store.list("taxpayer")}
    trust = None
    if new_trust:
        if not current.printed or not TRUST.search(current.printed):
            raise ValueError("no trust is named on that document")
        slug = slugify(name_key(current.printed)) or "trust"
        trust = Taxpayer(id=slug, name=current.printed, kind="trust")
        if slug in known:
            raise ValueError(f"{known[slug].name} is already known; choose it instead")
        taxpayers = [slug]
    elif any(t not in known for t in taxpayers):
        raise ValueError("choose people or trusts the vault knows")
    updated = current.model_copy(update={"taxpayers": taxpayers, "ask": not taxpayers})
    if updated == document.recipient:
        return
    who = trust.name if trust else ", ".join(taxpayers) or "nobody"
    tags = document.tags
    if current.taxpayers and not current.ask and CHANGED not in tags:
        tags = [*tags, CHANGED]  # TaxBinder had said whose it was: a correction
    with store.operation(actor, f"document {doc}: whose = {who}"):
        if trust is not None:
            store.put(trust, None)
        store.put(document.model_copy(update={"recipient": updated, "tags": tags}), rev)
