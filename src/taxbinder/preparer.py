"""For my preparer: papers handed to the preparer as they are, with a note: never typed,
matched, read by the AI model or asked about on Check. Shared by the CLI and the app."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from taxbinder import settings
from taxbinder.ingest.pdftext import pdf_info, sniff_mime
from taxbinder.models import Document
from taxbinder.models.document import Acquired, Classification
from taxbinder.store import RecordNotFound, RecordStore

TAG = "for-preparer"
NOTE_MAX = 120


def _note(note: str | None) -> str | None:
    note = " ".join((note or "").split())
    if len(note) > NOTE_MAX:
        raise ValueError(f"keep the note under {NOTE_MAX} characters")
    return note or None


def _check_year(year: int) -> None:
    if not 1990 <= year <= 2100:
        raise ValueError("give a tax year like 2025")


def add(store: RecordStore, name: str, data: bytes, year: int, note: str | None, actor: str) -> str:
    """Store a file for the preparer, as it is, for `year`. The same file already in the binder
    is marked instead. One change. Returns the document's id."""
    _check_year(year)
    note = _note(note)
    ext = Path(name).suffix.lstrip(".").lower()
    stored = store.blobs.put(data, ext)
    doc_id = f"doc_{stored.sha256[:12]}"
    try:
        store.get("document", doc_id)
    except RecordNotFound:
        pass
    else:
        mark(store, doc_id, year, note, actor)
        return doc_id
    mime = sniff_mime(data)
    doc = Document(
        id=doc_id,
        sha256=stored.sha256,
        blob=stored.path,
        original_filename=name,
        mime=mime,
        pages=pdf_info(data)[0] if mime == "application/pdf" else 1,
        bytes=len(data),
        acquired=Acquired(at=datetime.now(UTC), via="upload", filename=name),
        classification=Classification(tax_year=year, method="human", confirmed_by=actor),
        tags=[TAG],
        preparer_note=note,
    )
    with store.operation(actor, f"for my preparer {year}: {name}"):
        store.put(doc, None)
    return doc_id


def mark(store: RecordStore, doc: str, year: int, note: str | None, actor: str) -> None:
    """A file already in the binder, for the preparer as it is: off Check, out of the readers'
    way. One attached to a row or a gift is that row's document, so it stays as it is."""
    from taxbinder import check
    from taxbinder.review import attached_to

    _check_year(year)
    document, rev = store.get("document", doc)
    if attached_to(store, doc):
        raise ValueError("it's attached to a row or a gift: take it off there first")
    c = (document.classification or Classification()).model_copy(
        update={"doc_type": None, "tax_year": year, "method": "human", "confirmed_by": actor}
    )
    tags = [t for t in document.tags if t not in (check.SKIPPED, check.NOT_TAX, TAG)] + [TAG]
    update = {"classification": c, "tags": tags, "preparer_note": _note(note), "waiting": False}
    with store.operation(actor, f"for my preparer {year}: {document.original_filename}"):
        store.put(document.model_copy(update=update), rev)


def set_note(store: RecordStore, doc: str, note: str | None, actor: str) -> None:
    document, rev = store.get("document", doc)
    if TAG not in document.tags:
        raise ValueError("that file isn't one for your preparer")
    with store.operation(actor, f"for my preparer: note on {document.original_filename}"):
        store.put(document.model_copy(update={"preparer_note": _note(note)}), rev)


def unmark(store: RecordStore, doc: str, actor: str) -> None:
    """Not for the preparer after all: back to Check, to say what it is."""
    document, rev = store.get("document", doc)
    if TAG not in document.tags:
        raise ValueError("that file isn't one for your preparer")
    tags = [t for t in document.tags if t != TAG]
    update = {"tags": tags, "preparer_note": None, "waiting": True}
    with store.operation(actor, f"not for my preparer: {document.original_filename}"):
        store.put(document.model_copy(update=update), rev)


def is_paper(d: Any) -> bool:  # noqa: ANN401 - a Document
    return TAG in d.tags


def papers(store: RecordStore, year: int) -> list[Document]:
    """The year's papers for the preparer, oldest first."""
    return sorted(
        (d for d in store.list("document")
         if TAG in d.tags and d.classification and d.classification.tax_year == year),
        key=lambda d: d.acquired.at,
    )  # fmt: skip


def reminder(store: RecordStore, year: int) -> tuple[str, str] | None:
    """Last year's papers, when this year has none yet and the note wasn't set aside:
    (the notice's id, its text). A reminder only: nothing is expected or missing."""
    notice = f"preparer:{year}"
    if papers(store, year) or notice in settings.load(store).dismissed_notices:
        return None
    last = papers(store, year - 1)
    if not last:
        return None
    what = [d.preparer_note or d.original_filename for d in last]
    shown = ", ".join(what[:3]) + (f" and {len(what) - 3} more" if len(what) > 3 else "")
    n = len(last)
    return notice, (f"In {year - 1} you gave your preparer {n} paper{'s' if n != 1 else ''} "
                    f"as well ({shown}). Any for {year}?")  # fmt: skip
