"""Review actions shared by the CLI and the app: verify / unverify expectations, link evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from taxbinder.models import Expectation
from taxbinder.models.common import Link
from taxbinder.models.document import Classification
from taxbinder.models.expectation import Event
from taxbinder.returns import RETURN_TYPES, return_details
from taxbinder.store import RecordNotFound, RecordStore


def set_verified(store: RecordStore, ids: list[str], verified: bool, note: str, actor: str) -> None:
    """Mark expectations checked (review = verified) or undo it; one vault operation."""
    now = datetime.now(UTC)
    records = [store.get("expectation", exp_id) for exp_id in ids]
    verb, review = ("verify", "verified") if verified else ("unverify", "unreviewed")
    with store.operation(actor, f"{verb} {' '.join(ids)}"):
        for record, rev in records:
            event = Event(
                at=now, type="verified" if verified else "unverified", text=f"{actor}: {note}"
            )
            store.put(
                record.model_copy(update={"review": review, "events": [*record.events, event]}), rev
            )


def link_document(
    store: RecordStore,
    doc: str,
    target: str,
    actor: str,
    role: str = "primary",
    as_type: str | None = None,
) -> None:
    """Link a document as evidence for an expectation or a claim (ValueError if already linked)."""
    now = datetime.now(UTC)
    kind = "claim" if target.count("/") == 2 else "expectation"
    document, doc_rev = store.get("document", doc)
    record, rev = store.get(kind, target)
    if any(existing.doc == doc for existing in record.evidence):
        raise ValueError(f"{doc} is already linked to {target}")
    update: dict = {
        "evidence": [*record.evidence, Link(doc=doc, role=role, linked_at=now, linked_by=actor)]
    }
    if kind == "expectation" and record.acquisition in ("expected", "available", "received"):
        update["acquisition"] = "vaulted"
    with store.operation(actor, f"link {doc} -> {target}"):
        store.put(record.model_copy(update=update), rev)
        # as_type "none": just attach it ("another document"); don't guess what it is
        fixed = (
            None if as_type == "none" else _adopted(document.classification, record, kind, as_type)
        )
        if fixed is not None:
            store.put(document.model_copy(update={"classification": fixed}), doc_rev)


def set_applicability(
    store: RecordStore, exp_id: str, value: str, note: str | None, actor: str
) -> None:
    """Decide whether a document should exist this year (a note is required unless applicable)."""
    record, rev = store.get("expectation", exp_id)
    updated = Expectation.model_validate(
        {**record.model_dump(), "applicability": value, "applicability_note": note}
    )
    with store.operation(actor, f"applicability {exp_id} = {value}"):
        store.put(updated, rev)


def _type_for(record, kind: str) -> str | None:  # noqa: ANN001
    """What a document attached to this row or claim must be."""
    if kind == "expectation":
        return record.doc_type
    if record.category == "tax_payment":
        return "payment_confirmation"
    accepts = [t for r in record.requirements for t in r.accepts]
    return accepts[0] if accepts else None


def _adopted(c, record, kind: str, as_type: str | None = None):  # noqa: ANN001, ANN202
    """A person attached the document here: what the recogniser couldn't tell (its type, its
    source) is taken from where it was attached; what they said it is ("Attach transfer
    record") wins over what was read. Returns the new classification, or None."""
    from taxbinder.models.document import Classification

    c = c or Classification()
    update: dict = {}
    if as_type and as_type != c.doc_type:  # the person's word over the recogniser's
        update["doc_type"] = as_type
        update["method"] = "+".join(sorted({*(c.method or "rules").split("+"), "human"}))
    elif c.doc_type is None and (t := _type_for(record, kind)):
        update["doc_type"] = t
        update["method"] = "+".join(sorted({*(c.method or "rules").split("+"), "human"}))
    if kind == "expectation" and c.source is None:
        update["source"] = record.source
    # a gift's papers are never from before it: a year read earlier than that was misread
    if c.tax_year is None or (kind == "claim" and c.tax_year < record.year):
        update["tax_year"] = record.year
    return c.model_copy(update=update) if update else None


def adopt_linked_types(store: RecordStore, year: int, actor: str) -> int:
    """For files people attached (not the automatic matcher) whose type wasn't recognised, take the
    type from where they were attached. Returns how many documents changed."""
    changed = 0
    targets = [("expectation", r) for r in store.list("expectation", year)]
    targets += [("claim", r) for r in store.list("claim", year)]
    for kind, record in targets:
        for link in record.evidence:
            if link.linked_by.startswith("auto:") or link.role in ("superseded", "bill", "other"):
                continue
            try:
                document, rev = store.get("document", link.doc)
            except RecordNotFound:
                continue
            if document.classification and document.classification.doc_type:
                continue
            fixed = _adopted(document.classification, record, kind)
            if fixed is not None:
                with store.operation(actor, f"type of {document.id} from {record.id}"):
                    store.put(document.model_copy(update={"classification": fixed}), rev)
                changed += 1
    return changed


def _detached(record: Any, doc: str) -> dict:  # noqa: ANN401
    """The update that takes `doc` off a row or claim (its requirements are checked again)."""
    evidence = [link for link in record.evidence if link.doc != doc]
    update: dict = {"evidence": evidence}
    if hasattr(record, "requirements"):
        update["requirements"] = [
            r.model_copy(update={"satisfied_by": None}) if r.satisfied_by == doc else r
            for r in record.requirements
        ]
    elif not any(link.role != "superseded" for link in evidence):
        # a checklist row with no file left: back to expected, and no longer checked
        update |= {"acquisition": "expected", "review": "unreviewed"}
    return update


def attached_to(store: RecordStore, doc: str) -> list[Any]:
    return [
        r
        for r in [*store.list("expectation"), *store.list("claim")]
        if any(link.doc == doc for link in r.evidence)
    ]


def unlink_document(store: RecordStore, doc: str, target: str, actor: str) -> None:
    kind = "claim" if target.count("/") == 2 else "expectation"
    record, rev = store.get(kind, target)
    if not any(link.doc == doc for link in record.evidence):
        raise ValueError(f"{doc} isn't attached to {target}")
    with store.operation(actor, f"unlink {doc} from {target}"):
        store.put(record.model_copy(update=_detached(record, doc)), rev)


def edit_document(
    store: RecordStore, doc: str, actor: str, doc_type: str | None, tax_year: int | None
) -> None:
    """A person says what a file is and which year it's for."""
    from taxbinder import doctypes

    document, rev = store.get("document", doc)
    if doc_type and not doctypes.is_known(store, doc_type):
        raise ValueError(f"unknown document type {doc_type!r}")
    if tax_year is not None and not 1990 <= tax_year <= 2100:
        raise ValueError("give a tax year like 2025")
    old = document.classification or Classification()
    if (old.doc_type, old.tax_year) == (doc_type or None, tax_year):
        return
    methods = set((old.method or "").split("+")) - {""} | {"human"}
    fixed = old.model_copy(
        update={
            "doc_type": doc_type or None,
            "tax_year": tax_year,
            "method": "+".join(sorted(methods)),
            "confirmed_by": actor,
        }
    )
    update: dict = {"classification": fixed}
    if doc_type in RETURN_TYPES and document.tax_return is None:
        update["tax_return"] = return_details(store, document)  # so it shows under Returns
    with store.operation(actor, f"document {doc}: type {doc_type or '-'}, year {tax_year or '-'}"):
        store.put(document.model_copy(update=update), rev)


def delete_document(store: RecordStore, doc: str, actor: str) -> set[int]:
    """Take a file out of the vault's list and off everything it was attached to. The encrypted
    content stays in the vault's storage, so Undo can bring it back. Returns the years affected."""
    document, rev = store.get("document", doc)
    records = attached_to(store, doc)
    with store.operation(actor, f"delete document {document.original_filename}"):
        for record in records:
            kind = "claim" if record.id.count("/") == 2 else "expectation"
            _, record_rev = store.get(kind, record.id)
            store.put(record.model_copy(update=_detached(record, doc)), record_rev)
        store.delete("document", doc, rev)
    return {r.year for r in records}


def set_portal_url(store: RecordStore, exp_id: str, url: str, actor: str) -> str:
    """Save the portal address on the row's institution (all its rows, every year, share it).
    An address without a scheme gets https://; only https, no embedded user or password.
    Returns the saved address ('' when cleared)."""
    from urllib.parse import urlsplit

    from taxbinder.models.registry import Portal

    exp, _ = store.get("expectation", exp_id)
    source, _ = store.get("source", exp.source)
    inst, rev = store.get("institution", source.institution)
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    if url:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
            raise ValueError("give the portal's https:// address")
        if parts.username or parts.password:
            raise ValueError("leave the user name and password out of the address")
        if len(url) > 2000:
            raise ValueError("that address is too long")
    portal = (inst.portal or Portal()).model_copy(update={"url": url or None})
    if portal == inst.portal or (inst.portal is None and not url):
        return url
    with store.operation(actor, f"portal {inst.name}: {urlsplit(url).hostname or 'cleared'}"):
        store.put(inst.model_copy(update={"portal": portal}), rev)
    return url
