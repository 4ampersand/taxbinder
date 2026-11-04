"""Add documents: each file is checked and queued in its vault's inbox as it
arrives (`inbox/upload/<n>/<name>`), and a reader thread reads the queue in order, one file at
a time: text recognition outside the vault's lock, storing and reading under it, so pages stay
quick. A file left in the queue when the app quits is read next time; an exact copy is skipped
(same bytes, same document). A bad file is reported on its own and taken off the queue.

Each document goes through every step before the next starts (the Add documents page shows
them as a belt of stations): received, reading, recognised, sender, whose and year, then
(`each`, from the app) the amounts by the rules, the model's reading, the checks for its year;
then filed, or asked when something on it is unclear. `Item.steps` is how far it got,
`Item.found` what each step found, both for the page."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pypdfium2 as pdfium

from taxbinder import ocr, perf
from taxbinder.ingest.inbox import ingest_file, needs_ocr
from taxbinder.ingest.pdftext import pdf_info, sniff_mime
from taxbinder.pdfdoc import PDFIUM
from taxbinder.report import DOC_LABELS

if TYPE_CHECKING:
    from taxbinder.store import RecordStore
    from taxbinder.store.vault import Vault
    from taxbinder.web.app import AppState

log = logging.getLogger("taxbinder.add")

QUEUE = "upload"
MAX_BYTES = 50 * 1024 * 1024
KINDS = {  # what can be added, by content (never by the file's name)
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/heic": "heic",
    "text/html": "html",  # a page saved from a portal
}
PASSWORD = "has a password: open it, save a copy without one, and add that"
DAMAGED = "can't be read: the file may be damaged"


STEPS = ("received", "reading", "recognised", "sender", "whose", "amounts", "model", "checks")
ENDS = ("filed", "asked")  # the last step: nothing to ask, or something on it is unclear


@dataclass
class Item:
    name: str
    state: str = "queued"  # queued, reading, read, duplicate, bad
    note: str = ""  # the outcome in words
    steps: list[str] = field(default_factory=lambda: ["received"])  # reached, in order
    found: dict[str, Any] = field(default_factory=dict)  # what the steps found

    def reach(self, step: str, **found: Any) -> None:  # noqa: ANN401
        self.found.update(found)  # first, so a step reached always shows what it found
        self.steps.append(step)


@dataclass
class Batch:
    """What the Add documents page shows: every file of the current batch and its outcome."""

    id: str = ""  # the page's name for one drop or choice; a new one starts a new batch
    started: float = field(default_factory=time.monotonic)
    items: list[Item] = field(default_factory=list)
    finished: float | None = None
    _by_path: dict[str, Item] = field(default_factory=dict)

    @property
    def done(self) -> int:
        return sum(i.state in ("read", "duplicate", "bad") for i in self.items)

    @property
    def running(self) -> bool:
        return self.done < len(self.items)

    def summary(self) -> dict:
        items = list(self.items)
        done, total = self.done, len(items)
        reading = [i for i in items if i.state in ("read", "duplicate")]
        eta = None
        if self.running and len(reading) >= 2:
            each = (time.monotonic() - self.started) / done
            eta = round(each * (total - done))
        return {
            "total": total,
            "done": done,
            "bad": sum(i.state == "bad" for i in items),
            "running": self.running,
            "eta": eta,
            "items": [
                {"name": i.name, "state": i.state, "note": i.note, "steps": list(i.steps),
                 "found": dict(i.found)}  # fmt: skip
                for i in items
            ],
        }


def problem(data: bytes) -> str | None:
    """Why this file can't be added, in words; None when it can."""
    if not data:
        return "is empty"
    if len(data) > MAX_BYTES:
        return "is larger than 50 MB"
    mime = sniff_mime(data)
    if mime not in KINDS:
        return "isn't a PDF or a picture"
    if mime == "application/pdf":
        try:
            with PDFIUM:
                pdfium.PdfDocument(data).close()
        except pdfium.PdfiumError as e:
            return (
                PASSWORD
                if getattr(e, "err_code", None) == pdfium.raw.FPDF_ERR_PASSWORD
                else DAMAGED
            )
    return None


def queue_dir(vault: Vault) -> Path:
    return vault.inbox / QUEUE


def pending(vault: Vault) -> list[Path]:
    """The queued files, oldest first (a half-written one, `.name.part`, isn't)."""
    q = queue_dir(vault)
    if not q.is_dir():
        return []
    out = []
    for d in sorted(q.iterdir()):
        files = (
            [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
            if d.is_dir()
            else []
        )
        out.extend(files[:1])
    return out


def describe(store: RecordStore, doc_id: str) -> str:
    doc, _ = store.get("document", doc_id)
    c = doc.classification
    year = f" {c.tax_year}" if c and c.tax_year else ""
    if not (c and c.doc_type):
        return f"read{year} · what it is: not recognised yet"
    return f"read · {DOC_LABELS.get(c.doc_type, c.doc_type)}{year}"


def findings(store: RecordStore, doc_id: str) -> list[tuple[str, dict[str, Any]]]:
    """What reading a document found, step by step: what it is, who sent it, whose and when.
    Names only, never an account number or a TIN."""
    from taxbinder.store import RecordNotFound

    doc, _ = store.get("document", doc_id)
    c = doc.classification
    kind = DOC_LABELS.get(c.doc_type, c.doc_type) if c and c.doc_type else None
    row = None  # the row it was matched to: the sender and whose when the page didn't say
    try:
        row = store.get("source", c.source)[0] if c and c.source else None
    except RecordNotFound:
        row = None
    issuer, sender_by = (c.issuer, "the page") if c and c.issuer else (None, None)
    if issuer is None and row is not None:
        issuer, sender_by = row.institution, "its row"
    try:
        sender = store.get("institution", issuer)[0].name if issuer else None
    except RecordNotFound:
        sender = sender_by = None
    names = {t.id: t.name for t in store.list("taxpayer")}
    r = doc.recipient
    whose, whose_by = [], None
    if r and r.taxpayers and not r.ask:
        whose, whose_by = [names.get(t, t) for t in r.taxpayers], "the page"
    elif r is None and row is not None and row.owner in names:
        whose, whose_by = [names[row.owner]], "its row"
    asks = [q for q, unclear in (("what it is", kind is None), ("who sent it", sender is None),
            ("whose it is", r is not None and r.ask), ("the year", not (c and c.tax_year)))
            if unclear]  # fmt: skip
    return [
        ("recognised", {"kind": kind, "doc": doc.id}),
        ("sender", {"sender": sender, "sender_by": sender_by}),
        ("whose", {"whose": whose, "whose_by": whose_by, "year": c.tax_year if c else None,
                   "asks": asks}),  # fmt: skip
    ]


class Reader:
    """The queue's reader: one thread, woken when a file arrives (and once at start, for what
    was left last time). `finish(store, years, matched)` re-runs the checks once a queue is
    empty (matched: new documents linked to a row, for their figures)."""

    def __init__(
        self,
        state: AppState,
        finish: Callable[[RecordStore, set[int], set[str]], None],
        each: Callable[[RecordStore, str, int | None, Item], None] | None = None,
    ) -> None:
        self.state = state
        self.finish = finish
        self.each = each  # one new document's remaining steps (amounts, model, checks)
        self.batch = Batch()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()  # numbering the queue
        self._thread: threading.Thread | None = None
        self._actors: dict[str, str] = {}  # queued file → who added it (this run only)

    def clear(self) -> None:
        """Another binder opened (the demo, or yours again): its Add page starts empty. The
        caller makes sure nothing is still being read (`batch.running`)."""
        self.batch = Batch()

    # -- receiving (request threads) ------------------------------------------------------

    def add(self, store: RecordStore, name: str, data: bytes, actor: str, batch: str = "") -> Item:
        """Check a file and queue it; a bad one is reported in the batch straight away."""
        if (why := problem(data)) is not None:
            return self.reject(name, why, batch)
        item = self._item(name, batch)
        with self._lock:
            q = queue_dir(store.vault)
            q.mkdir(mode=0o700, parents=True, exist_ok=True)
            n = max((int(d.name) for d in q.iterdir() if d.name.isdigit()), default=0) + 1
            d = q / f"{n:06d}"
            d.mkdir(mode=0o700)
        part = d / f".{name}.part"
        part.write_bytes(data)
        part.rename(d / name)
        self.batch._by_path[str(d / name)] = item
        self._actors[str(d / name)] = actor
        self.wake()
        return item

    def reject(self, name: str, why: str, batch: str = "") -> Item:
        item = self._item(name, batch)
        item.state, item.note = "bad", why
        return item

    def _item(self, name: str, batch: str) -> Item:
        """A new line in the batch: a new drop starts a new batch once the last one is read
        (while it's still reading, the new files join it)."""
        if batch != self.batch.id:
            if not self.batch.running:
                self.batch = Batch()
            self.batch.id = batch
        item = Item(name)
        self.batch.items.append(item)
        return item

    def wake(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="add-reader", daemon=True)
            self._thread.start()
        self._wake.set()

    # -- reading (the reader thread) ------------------------------------------------------

    def _stores(self) -> list[RecordStore]:
        from taxbinder.entities import list_entities

        with self.state.lock:
            refs = list_entities(self.state.vault_path) if self.state.store is not None else []
            stores = [self.state.store_for(r.slug) for r in refs]
        return [s for s in stores if s is not None]

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=5)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                for store in self._stores():
                    self.read_queue(store)
            except Exception:  # noqa: BLE001 - keep reading; the file stays for a retry
                log.exception("reading the queue failed")
            if not self.batch.running and self.batch.items and self.batch.finished is None:
                self.batch.finished = time.monotonic()

    def read_queue(self, store: RecordStore) -> None:
        years: set[int] = set()
        matched: set[str] = set()
        while files := pending(store.vault):
            year, doc = self.read_one(store, files[0])
            years |= {year} if year else set()
            matched |= {doc} if doc else set()
        if years or matched:
            with self.state.lock, perf.step("add.finish"):
                self.finish(store, years, matched)

    def read_one(self, store: RecordStore, path: Path) -> tuple[int | None, str | None]:
        """Read one queued file and take it off the queue: its tax year, and its id when it's
        new and matched to a row."""
        item = self.batch._by_path.get(str(path))
        if item is None:  # left from last time
            item = Item(path.name, note="left from last time")
            self.batch.items.append(item)
            self.batch._by_path[str(path)] = item
        actor = self._actors.pop(str(path), self.state.local_actor)
        year = doc_id = None
        try:
            data = path.read_bytes()
            if (why := problem(data)) is not None:
                item.state, item.note = "bad", why
                return None, None
            mime = sniff_mime(data)
            text = pdf_info(data)[1] if mime == "application/pdf" else ""
            item.state = "reading"
            item.reach("reading", scan=needs_ocr(mime, text))
            pages = None
            if needs_ocr(mime, text):
                item.note = (
                    "photo, reading text…" if mime.startswith("image/") else "scan, reading text…"
                )
                with perf.step("add.ocr"):
                    pages = ocr.read(data, mime)  # outside the lock: the slow part
            with self.state.lock, perf.step("add.ingest"):
                result = ingest_file(store, path, "upload", actor, pages=pages, waiting=True)
                if result.duplicate:
                    item.state, item.note = "duplicate", "already added, skipped"
                    return None, None
                note = describe(store, result.doc_id)
                doc, _ = store.get("document", result.doc_id)
                year = doc.classification.tax_year if doc.classification else None
                doc_id = doc.id if result.linked_to else None
                found = findings(store, doc.id)
            for step, what in found:
                item.reach(step, **what)
            if self.each is not None:
                try:
                    with perf.step("add.checks"):
                        self.each(store, doc.id, year, item)
                except Exception:  # noqa: BLE001 - it's stored; the checks run again later
                    log.exception("a new document's checks failed")
            item.reach(ENDS[bool(item.found.get("asks"))])
            item.state, item.note = "read", note
        except Exception:  # noqa: BLE001 - one bad file never stops the rest
            log.exception("a queued file couldn't be read")
            item.state, item.note = "bad", DAMAGED
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return year, doc_id
