"""Inbox → vault ingestion (§6). One store operation per file; the inbox file is removed
only after it has been stored, so a crash means a retry, never a loss.

`inbox/` takes manual drops; `inbox/portal/` is where the desktop shell saves portal downloads,
optionally with `<name>.meta.json` holding the page URL.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from taxbinder import ocr, slack
from taxbinder.extract.parties import read_parties
from taxbinder.ingest.classify import classify
from taxbinder.ingest.match import match
from taxbinder.ingest.pdftext import pdf_info, sniff_mime
from taxbinder.institutions import remember
from taxbinder.models import Document
from taxbinder.models.common import Link
from taxbinder.models.document import Acquired, Classification, Version
from taxbinder.models.expectation import Event
from taxbinder.pdfdoc import Page
from taxbinder.preparer import TAG as PAPER
from taxbinder.returns import RETURN_TYPES, with_reading
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.whose import recipient_of

log = logging.getLogger(__name__)

PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial")
SETTLE_SECONDS = 1.0


@dataclass
class Ingested:
    doc_id: str
    duplicate: bool = False
    linked_to: str | None = None
    superseded: str | None = None
    candidates: list[tuple[str, int]] = field(default_factory=list)


def _strip_query(url: str | None) -> str | None:
    return re.sub(r"[?#].*$", "", url) if url else None


def settled_files(inbox: Path, now: float | None = None) -> list[tuple[Path, str]]:
    """Files ready to ingest: not hidden, not partial downloads, not still being written."""
    now = now or time.time()
    out = []
    for folder, via in ((inbox, "drop_folder"), (inbox / "portal", "portal_window")):
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            name = p.name.lower()
            if not p.is_file() or name.startswith(".") or name.endswith(".meta.json"):
                continue
            if name.endswith(PARTIAL_SUFFIXES) or now - p.stat().st_mtime < SETTLE_SECONDS:
                continue
            out.append((p, via))
    return out


def ingest_file(
    store: RecordStore,
    path: Path,
    via: str,
    actor: str = "auto:ingest",
    now: datetime | None = None,
    pages: list[Page] | None = None,
    waiting: bool = False,
) -> Ingested:
    """Store a file and read it. A scan or photo is read on the Mac first (`pages`: already
    recognised, outside the vault's lock, by a caller that could). `waiting`: it goes to Check
    before it's filed (Add documents)."""
    now = now or datetime.now(UTC)
    data = path.read_bytes()
    meta_path = path.with_name(path.name + ".meta.json")
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except ValueError:
            log.warning("unreadable .meta.json next to an inbox file")
    acquired = Acquired(
        at=now, via=via, filename=path.name, source_url=_strip_query(meta.get("source_url"))
    )

    stored = store.blobs.put(data, path.suffix.lstrip(".").lower())
    doc_id = f"doc_{stored.sha256[:12]}"
    try:
        existing, rev = store.get("document", doc_id)
    except RecordNotFound:
        existing, rev = None, None

    if existing is not None:  # same bytes again: record the extra acquisition only
        with store.operation(actor, f"ingest: duplicate of {doc_id}"):
            store.put(
                existing.model_copy(update={"reacquired": [*existing.reacquired, acquired]}), rev
            )
        _remove(path, meta_path)
        return Ingested(doc_id, duplicate=True)

    mime = sniff_mime(data)
    page_count, text = pdf_info(data) if mime == "application/pdf" else (None, "")
    if pages is None and needs_ocr(mime, text):
        pages = ocr.read(data, mime)
    words = None
    if pages:
        text = ocr.text_of(pages)
        words = store.blobs.put(ocr.dumps(pages), "ocr.json")  # encrypted like the documents
    c, target, source, candidates = _classify_and_match(
        store, text, path.name, now, data, pages or None
    )
    result = Ingested(doc_id, candidates=candidates)
    issuer_record = None
    if c.issuer_name and c.doc_type not in RETURN_TYPES:
        found, issuer_record = remember(
            c.issuer_name, c.issuer_tin, c.doc_type, store.list("institution"), c.scanned
        )
        if c.issuer not in (None, found):
            issuer_record = None  # named otherwise in the vault (a sponsor by its file names)
        else:
            c.issuer = found
    on_row = _supersedes(store, target, c)  # the row's own document, replaced
    supersedes = on_row
    if on_row is None and c.is_corrected:
        supersedes = _corrects(store, c)  # no row links them: the same form, earlier

    doc = Document(
        id=doc_id,
        sha256=stored.sha256,
        blob=stored.path,
        original_filename=path.name,
        mime=mime,
        pages=page_count if mime == "application/pdf" else 1 if pages else None,
        bytes=len(data),
        acquired=acquired,
        classification=Classification(
            doc_type=c.doc_type,
            tax_year=c.tax_year,
            issuer=c.issuer,
            source=source,
            account_number=c.account_number,
            issuer_tin=c.issuer_tin,
            confidence=c.confidence,
            fiscal_year_end=c.fiscal_year_end,
            method=c.method,
        ),
        version=Version(supersedes=supersedes, is_corrected=c.is_corrected),
        tax_return=with_reading(c.tax_return, data) if c.tax_return else None,
        recipient=recipient_of(
            c.recipient_name, c.recipient_last4, store.list("taxpayer"), c.scanned
        )
        if c.recipient_name
        else None,
        ocr=words.sha256 if words else None,
        waiting=waiting,
    )

    label = f"{c.doc_type or 'unclassified'} {c.tax_year or ''} {source or 'unmatched'}"
    with store.operation(actor, f"ingest: {label} ({doc_id})"):
        if issuer_record is not None:  # a new institution, or a new spelling or EIN of one
            try:
                _, inst_rev = store.get("institution", issuer_record.id)
            except RecordNotFound:
                inst_rev = None
            store.put(issuer_record, inst_rev)
        store.put(doc, None)
        if target is not None:
            _link(store, target, doc_id, on_row, now)
            result.linked_to = target.id
        result.superseded = supersedes
    _remove(path, meta_path)
    return result


def needs_ocr(mime: str, text: str) -> bool:
    """A photo, or a PDF with (almost) no text layer: a scan."""
    if mime in ("image/jpeg", "image/png", "image/heic"):
        return True
    return mime == "application/pdf" and len(text.strip()) < ocr.TEXT_LAYER


def scanned_pages(store: RecordStore, doc: Document) -> list[Page] | None:
    """What was recognised on a scanned document, from the vault's encrypted cache."""
    return ocr.loads(store.blobs.get(doc.ocr)) if doc.ocr else None


def _classify_and_match(  # noqa: ANN202
    store: RecordStore,
    text: str,
    filename: str,
    now: datetime,
    data: bytes | None = None,
    pages: list[Page] | None = None,
):
    """(classified, target expectation or None, its source id or None, top candidates)."""
    institutions = store.list("institution")
    c = classify(text, filename, institutions, now.year, store.list("doctype"), data, pages)
    if c.tax_year is None or c.doc_type in RETURN_TYPES:  # a return isn't any row's document
        return c, None, None, []
    m = match(
        c,
        text,
        filename,
        store.list("expectation", c.tax_year),
        store.list("source"),
        institutions,
        [t.name for t in store.list("taxpayer")],
    )
    candidates = [(x.expectation.id, x.score) for x in m.candidates[:5]]
    if m.best:
        return c, m.best.expectation, m.best.source.id, candidates
    return c, None, None, candidates


def _supersedes(store: RecordStore, target, c) -> str | None:  # noqa: ANN001
    """The row's document a corrected form replaces: never one from another issuer or account
    (a row the form was wrongly matched to keeps its document)."""
    if target is None or not c.is_corrected:
        return None
    for link in target.evidence:
        if link.role == "primary":
            try:
                doc, _ = store.get("document", link.doc)
            except RecordNotFound:
                continue
            return doc.id if _same_form(doc.classification, c) else None
    return None


def _ending(account: str | None) -> str:
    return re.sub(r"\D", "", slack.digits(account or ""))[-4:]  # "…33O4" read from a scan


def _same_form(k: Classification | None, c) -> bool:  # noqa: ANN001
    """An earlier document is the same form as `c`: issuer and account agree where both are
    known."""
    if k is None:
        return True
    if k.issuer and c.issuer and k.issuer != c.issuer:
        return False
    mine, theirs = _ending(c.account_number), _ending(k.account_number)
    return not (mine and theirs and mine != theirs)


def _corrects(store: RecordStore, c) -> str | None:  # noqa: ANN001
    """The document a corrected form replaces when no row links them: the latest one of the
    same type, year and issuer (and account, when both print one) that isn't replaced already."""
    if not (c.doc_type and c.tax_year and c.issuer):
        return None
    replaced = {d.version.supersedes for d in store.list("document")}
    same = [
        d
        for d in store.list("document")
        if d.id not in replaced
        and (k := d.classification) is not None
        and (k.doc_type, k.tax_year, k.issuer) == (c.doc_type, c.tax_year, c.issuer)
        and _same_form(k, c)
    ]
    return max(same, key=lambda d: d.acquired.at).id if same else None


def fill_issuer_tins(
    store: RecordStore, actor: str, lock: AbstractContextManager | None = None
) -> int:
    """Documents added before each kept its issuer's EIN get it by reading their first
    page again: the PDF's text, or the scan's cached text; no OCR, no model. `lock` is held
    only while the vault is used, not while reading. Returns how many got one."""
    guard = lock or nullcontext()
    with guard:
        todo = [
            d for d in store.list("document")
            if (k := d.classification) is not None and k.issuer_tin is None
            and k.doc_type not in RETURN_TYPES and PAPER not in d.tags  # papers: never read
        ]  # fmt: skip
    found = {}
    for doc in todo:
        with guard:
            data = store.blobs.get(doc.sha256)
            pages = scanned_pages(store, doc)
        if doc.mime != "application/pdf" and not pages:
            continue
        try:
            tin = read_parties(data, doc.classification.doc_type, pages).issuer_tin
        except Exception as e:  # noqa: BLE001 - one unreadable file stops nothing
            log.debug("issuer's EIN not read again (%s)", type(e).__name__)
            continue
        if tin:
            found[doc.id] = tin
    if not found:
        return 0
    with guard, store.operation(actor, f"issuers' EINs kept with {len(found)} documents"):
        for doc_id, tin in found.items():
            doc, rev = store.get("document", doc_id)
            if doc.classification and doc.classification.issuer_tin is None:
                c = doc.classification.model_copy(update={"issuer_tin": tin})
                store.put(doc.model_copy(update={"classification": c}), rev)
    return len(found)


def use_model_tin(store: RecordStore, doc_id: str, actor: str) -> bool:
    """A document whose EIN the rules didn't read takes the one the model read, if it's
    usable (`institutions.model_tin`). Not linked to a row yet, it then belongs to the issuer
    with that EIN, or to a new one; linked, its link stays (never moved) and its issuer only
    learns the EIN if it has none. Returns whether anything changed."""
    from taxbinder.institutions import model_tin
    from taxbinder.modelread import reading

    doc, rev = store.get("document", doc_id)
    c = doc.classification
    if c is None or c.issuer_tin or c.doc_type in RETURN_TYPES:
        return False
    institutions = {i.id: i for i in store.list("institution")}
    parties = reading(store, doc).get("parties", {})
    tin = model_tin(parties.get("sender_tin", {}), {i.tin for i in institutions.values() if i.tin})
    if tin is None:
        return False
    update: dict = {"issuer_tin": tin}
    record = None
    linked = any(x.doc == doc_id for e in store.list("expectation") for x in e.evidence)
    sender = parties.get("sender", {})
    name = institutions[c.issuer].name if c.issuer in institutions else (
        sender.get("value") if sender.get("printed") else None
    )
    if not linked and name:
        update["issuer"], record = remember(name, tin, c.doc_type, list(institutions.values()))
    elif c.issuer in institutions and institutions[c.issuer].tin is None:
        record = institutions[c.issuer].model_copy(update={"tin": tin})
    with store.operation(actor, f"model: the issuer's EIN for {doc_id}"):
        if record is not None:
            try:
                inst_rev = store.get("institution", record.id)[1]
            except RecordNotFound:
                inst_rev = None
            store.put(record, inst_rev)
        store.put(doc.model_copy(update={"classification": c.model_copy(update=update)}), rev)
    return True


def rematch(store: RecordStore, actor: str, now: datetime | None = None) -> list[Ingested]:
    """Match again every document linked to nothing (after matching improved, or rows were added).

    Documents that are linked somewhere are left alone: an existing link is never moved.
    """
    now = now or datetime.now(UTC)
    linked = {x.doc for e in store.list("expectation") for x in e.evidence}
    out = []
    for doc in store.list("document"):
        if doc.id in linked or (doc.classification and doc.classification.doc_type in RETURN_TYPES):
            continue
        if PAPER in doc.tags:  # for my preparer, as it is: never matched
            continue
        data = store.blobs.get(doc.sha256)
        text = pdf_info(data)[1] if doc.mime == "application/pdf" else ""
        pages = scanned_pages(store, doc)
        if pages:
            text = ocr.text_of(pages)
        c, target, source, candidates = _classify_and_match(
            store, text, doc.original_filename, now, data, pages
        )
        result = Ingested(doc.id, candidates=candidates)
        if target is not None:
            supersedes = _supersedes(store, target, c)
            _, rev = store.get("document", doc.id)
            classification = (doc.classification or Classification()).model_copy(
                update={
                    "doc_type": c.doc_type,
                    "tax_year": c.tax_year,
                    "issuer": c.issuer,
                    "source": source,
                    "confidence": c.confidence,
                    "method": c.method,
                }
            )
            with store.operation(actor, f"rematch: {doc.id} -> {target.id}"):
                store.put(doc.model_copy(update={"classification": classification}), rev)
                _link(store, target, doc.id, supersedes, now)
            result.linked_to, result.superseded = target.id, supersedes
            linked.add(doc.id)
        out.append(result)
    return out


def _link(store: RecordStore, e, doc_id: str, supersedes: str | None, now: datetime) -> None:  # noqa: ANN001
    current, rev = store.get("expectation", e.id)
    evidence = [
        link.model_copy(update={"role": "superseded"}) if link.doc == supersedes else link
        for link in current.evidence
    ]
    evidence.append(Link(doc=doc_id, role="primary", linked_at=now, linked_by="auto:matcher"))
    update: dict = {"evidence": evidence, "acquisition": "vaulted"}
    events = list(current.events)
    if supersedes:
        was_verified = current.review == "verified"
        update["review"] = "unreviewed"
        if current.delivery == "sent":
            update["delivery"] = "stale"
        events.append(
            Event(
                at=now,
                type="superseded_after_verification" if was_verified else "superseded",
                text=f"{supersedes} → {doc_id}",
            )
        )
    update["events"] = events
    store.put(current.model_copy(update=update), rev)


def _remove(path: Path, meta_path: Path) -> None:
    path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def scan_inbox(
    store: RecordStore,
    actor: str = "auto:ingest",
    inbox: Path | None = None,
    waiting: bool = False,
) -> list[Ingested]:
    """Ingest every settled inbox file (by default the store's own inbox); a failing file
    stays for the next scan. `waiting`: they go to Check (the app's watcher)."""
    out = []
    for path, via in settled_files(inbox or store.vault.inbox):
        try:
            out.append(ingest_file(store, path, via, actor, waiting=waiting))
        except Exception:  # noqa: BLE001 - keep going; the file is retried next time
            log.exception("ingest failed for an inbox file")
    return out
