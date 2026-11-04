"""Private details to hide when redacting (§9.2): add, remove, list masked."""

from __future__ import annotations

from taxbinder.models.privacy import PrivacyList, PrivateValue
from taxbinder.store import RecordNotFound, RecordStore

KINDS = {
    "name": "Name",
    "address": "Street address",
    "number": "Number (account, ID…)",
    "email": "Email",
    "phone": "Phone",
    "other": "Other",
}


def _load(store: RecordStore) -> tuple[PrivacyList, str | None]:
    try:
        return store.get("privacy", "privacy")
    except RecordNotFound:
        return PrivacyList(), None


def values(store: RecordStore) -> list[PrivateValue]:
    return list(_load(store)[0].values)


def masked(v: PrivateValue) -> str:
    """Enough to recognise an entry without showing it: initials, or the last 4 of a number."""
    s = v.secret
    if v.kind in ("number", "phone"):
        digits = "".join(ch for ch in s if ch.isalnum())
        return "…" + digits[-4:]
    if v.kind == "email":
        return s[0] + "…@…"
    return " ".join(w[0] + "…" for w in s.split())


def add_value(store: RecordStore, kind: str, secret: str, note: str, actor: str) -> int:
    """Add a value (ignored when it's already there); returns how many there are."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; one of: {', '.join(KINDS)}")
    value = PrivateValue(kind=kind, secret=secret, note=note.strip())
    current, rev = _load(store)
    if any(
        v.kind == value.kind and v.secret.casefold() == value.secret.casefold()
        for v in current.values
    ):
        return len(current.values)
    with store.operation(actor, f"privacy: add a {kind}"):
        store.put(current.model_copy(update={"values": [*current.values, value]}), rev)
    return len(current.values) + 1


def remove_value(store: RecordStore, index: int, actor: str) -> None:
    current, rev = _load(store)
    if not 0 <= index < len(current.values):
        raise ValueError("no such entry")
    kept = [v for i, v in enumerate(current.values) if i != index]
    with store.operation(actor, f"privacy: remove a {current.values[index].kind}"):
        store.put(current.model_copy(update={"values": kept}), rev)
