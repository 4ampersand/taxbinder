"""Get documents out of the vault: one file, or many as a zip (app and browser downloads).

Names follow the CPA package (`Institution - Source - K1.pdf`, account-like digit runs removed).
Blobs are read through the blob store, which checks each one against its hash.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import IO, Any

from taxbinder.package.build import LABELS, file_name
from taxbinder.store import RecordStore


class Namer:
    """Download names for documents, from the records they are linked to."""

    def __init__(self, store: RecordStore) -> None:
        self.sources = {s.id: s for s in store.list("source")}
        self.institutions = {i.id: i for i in store.list("institution")}
        self.labels = {**LABELS, **{d.id: d.name for d in store.list("doctype")}}
        self.linked: dict[str, list[Any]] = defaultdict(list)
        for record in [*store.list("expectation"), *store.list("claim")]:
            for link in record.evidence:
                if link.role != "superseded":
                    self.linked[link.doc].append(record)

    def name(self, doc: Any) -> str:  # noqa: ANN401
        c = doc.classification
        linked = self.linked.get(doc.id, [])
        source_id = (c.source if c else None) or next(
            (getattr(r, "source", None) for r in linked if getattr(r, "source", None)), None
        )
        source = self.sources.get(source_id) if source_id else None
        inst_id = source.institution if source else (c.issuer if c else None)
        inst = self.institutions.get(inst_id or "")
        doc_type = (c.doc_type if c else None) or next(
            (getattr(r, "doc_type", None) for r in linked if getattr(r, "doc_type", None)), None
        )
        return file_name(doc, doc_type, inst, source, linked, self.labels)


def documents(store: RecordStore, ids: list[str]) -> list[Any]:
    """The documents for these ids, in order, without repeats (RecordNotFound for unknown ids)."""
    seen: dict[str, Any] = {}
    for doc_id in ids:
        if doc_id not in seen:
            seen[doc_id] = store.get("document", doc_id)[0]
    return list(seen.values())


def year_document_ids(store: RecordStore, year: int) -> list[str]:
    """Every file attached to the year's checklist rows and claims (superseded copies excluded)."""
    ids: list[str] = []
    for record in [*store.list("expectation", year), *store.list("claim", year)]:
        ids += [link.doc for link in record.evidence if link.role != "superseded"]
    return list(dict.fromkeys(ids))


def unique(name: str, used: set[str]) -> str:
    stem, suffix, n = Path(name).stem, Path(name).suffix, 2
    candidate = name
    while candidate.lower() in used:
        candidate, n = f"{stem} ({n}){suffix}", n + 1
    used.add(candidate.lower())
    return candidate


def write_zip(store: RecordStore, docs: list[Any], out: IO[bytes]) -> int:
    """Write the documents into a zip; returns the number of files."""
    namer, used = Namer(store), set()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for doc in docs:
            z.writestr(unique(namer.name(doc), used), store.blobs.get(doc.sha256))
    return len(docs)


def zip_file(store: RecordStore, docs: list[Any]) -> IO[bytes]:
    """The zip in a temporary file (kept in memory only while small), rewound for reading."""
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115 - returned
    write_zip(store, docs, out)
    out.seek(0)
    return out


def zip_name(year: int | None, today: dt.date) -> str:
    return f"TaxBinder {year} documents.zip" if year else f"TaxBinder documents {today}.zip"


def save(
    store: RecordStore, docs: list[Any], folder: Path, zip_as: str, *, as_zip: bool = False
) -> Path:
    """Save to `folder` (the app window's Downloads): one document as itself, several (or
    `as_zip`, e.g. a whole year) as a zip.

    Never overwrites: an existing name gets " (2)", " (3)"….
    """
    if not docs:
        raise ValueError("no documents selected")
    folder.mkdir(parents=True, exist_ok=True)
    used = {p.name.lower() for p in folder.iterdir()}
    if len(docs) == 1 and not as_zip:
        target = folder / unique(Namer(store).name(docs[0]), used)
        target.write_bytes(store.blobs.get(docs[0].sha256))
    else:
        target = folder / unique(zip_as, used)
        with target.open("wb") as f:
            write_zip(store, docs, f)
    return target


def unique_path(folder: Path, name: str) -> Path:
    """`folder/name`, or `name (2)` … when taken."""
    used = {p.name.lower() for p in folder.iterdir()} if folder.exists() else set()
    return folder / unique(name, used)
