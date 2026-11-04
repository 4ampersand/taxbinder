"""Document types: the built-in catalog plus the ones added in the app (`doctype` records)."""

from __future__ import annotations

import re

from taxbinder.models import DocTypeDef
from taxbinder.models.common import BUILTIN_DOC_TYPES
from taxbinder.report import DOC_LABELS
from taxbinder.store import RecordNotFound, RecordStore


def custom_types(store: RecordStore) -> list[DocTypeDef]:
    return sorted(store.list("doctype"), key=lambda d: d.name.lower())


def labels(store: RecordStore) -> dict[str, str]:
    """Human label per type id, built-in and added."""
    return {**DOC_LABELS, **{d.id: d.name for d in custom_types(store)}}


def is_known(store: RecordStore, doc_type: str) -> bool:
    if doc_type in BUILTIN_DOC_TYPES:
        return True
    try:
        store.get("doctype", doc_type)
        return True
    except (RecordNotFound, ValueError):
        return False


def type_id(name: str) -> str:
    """'Form 592-B' → 'x-form-592-b'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    if not slug:
        raise ValueError("the document type needs a name with letters or digits")
    return f"x-{slug}"


def add_doc_type(
    store: RecordStore, name: str, folder: str, keywords: list[str], actor: str
) -> DocTypeDef:
    """Add a document type; an existing one with the same name is returned unchanged."""
    name = name.strip()
    lowered = {label.lower() for label in DOC_LABELS.values()}
    if name.lower() in lowered:
        raise ValueError(f"{name!r} is already a built-in document type")
    new = DocTypeDef(
        id=type_id(name),
        name=name,
        folder=folder or "09-Other",  # type: ignore[arg-type]
        keywords=[k.strip() for k in keywords if k.strip()][:10],
    )
    try:
        existing, _ = store.get("doctype", new.id)
        return existing
    except RecordNotFound:
        pass
    with store.operation(actor, f"doctype add {new.id}"):
        store.put(new, None)
    return new


def looks_like_return(d: DocTypeDef) -> bool:
    """An added type that is probably the person's own name for tax returns."""
    return bool(re.search(r"\breturns?\b", d.name, re.IGNORECASE))


def merge_doc_type(store: RecordStore, old: str, new: str, actor: str) -> int:
    """Every document of the added type `old` becomes `new` (one change, undoable). Moving to
    the built-in return type also records what kind of return each one is. The added type is
    removed once nothing uses it. Returns how many documents moved."""
    from taxbinder.returns import RETURN_TYPES, return_details

    source, source_rev = store.get("doctype", old)
    if new == old or not is_known(store, new):
        raise ValueError(f"unknown document type {new!r}")
    docs = [
        d for d in store.list("document") if d.classification and d.classification.doc_type == old
    ]
    rows = [e for e in store.list("expectation") if e.doc_type == old]
    with store.operation(actor, f"doctype merge {old} into {new} ({len(docs)} documents)"):
        for doc in docs:
            _, rev = store.get("document", doc.id)
            c = doc.classification
            assert c is not None
            update: dict = {}
            year = c.tax_year
            if new in RETURN_TYPES and doc.tax_return is None:
                update["tax_return"] = return_details(store, doc)
            update["classification"] = c.model_copy(update={"doc_type": new, "tax_year": year})
            store.put(doc.model_copy(update=update), rev)
        if not rows:
            store.delete("doctype", old, source_rev)
    return len(docs)
