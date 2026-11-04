"""Documents read with the model in the background: one thread, one engine.

Every document of a form the cross-check knows (`figures.FORM_OF`, a consolidated 1099) that
hasn't been read with the model in use is read with it (`extract/crosscheck.py`); the result is
kept with the document, encrypted (`Document.model_read`), and Across the years' suggestions for it
are read again from it. It works through what's waiting, newest first, then looks again every
`POLL` seconds; without a model (none downloaded, turned off, too little memory) it does
nothing. The engine stops by itself when there's nothing to read (`engine.IDLE`).

A document just added is read straight away, in the Add documents reader's turn (`read_now`),
so the person sees the model's reading before the next document starts; one engine, so
one reading at a time.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from taxbinder import perf

if TYPE_CHECKING:
    from taxbinder.store import RecordStore
    from taxbinder.web.app import AppState

log = logging.getLogger(__name__)

POLL = 30.0  # seconds between looks for documents to read
ACTOR = "auto:model"


def wanted(doc: Any, model: str, store: RecordStore) -> bool:  # noqa: ANN401 - a Document
    from taxbinder.extract.figures import FORM_OF

    c = doc.classification
    if c is None or (c.doc_type not in FORM_OF and c.doc_type != "1099_composite"):
        return False
    return doc.model_read is None or reading(store, doc).get("model") != model


@perf.timed("modelread.reading", many=True)
def reading(store: RecordStore, doc: Any) -> dict[str, Any]:  # noqa: ANN401
    """What the model read on a document ({} if it hasn't been)."""
    if doc.model_read is None:
        return {}
    try:
        return json.loads(store.blobs.get(doc.model_read))
    except (OSError, ValueError):
        return {}


class ModelReader:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.engine: Any = None  # engine.Engine for the model in use
        self.waiting = 0  # documents left, for Settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()  # one reading at a time: there's one engine

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="model-reader", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.engine is not None:
            self.engine.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.work()
            except Exception as e:  # noqa: BLE001 - never takes the app down; tried again later
                if not self._stop.is_set():  # stopping: the engine went first, as it should
                    log.warning("reading with the model stopped (%s)", type(e).__name__)
            self._wake.wait(POLL)
            self._wake.clear()

    def _engine(self, model: Any) -> Any:  # noqa: ANN401 - an llm.Model
        from taxbinder.engine import Engine

        where = self.state.models_dir
        path = where / model.model.name
        if self.engine is None or self.engine.model != path:
            if self.engine is not None:
                self.engine.stop()
            mmproj = where / model.mmproj.name if model.mmproj else None
            self.engine = Engine(path, mmproj, pidfile=where / "engine.pid")
        return self.engine

    def _todo(self, model: str) -> list[tuple[RecordStore, str]]:
        with self.state.lock:
            stores = [s for s in (self.state.store, *self.state._entity_stores.values()) if s]
            todo = []
            for store in stores:
                docs = [d for d in store.list("document") if wanted(d, model, store)]
                docs.sort(key=lambda d: d.acquired.at, reverse=True)  # the newest first
                todo += [(store, d.id) for d in docs]
            return todo

    def work(self) -> int:
        """Read what's waiting; how many were read."""
        from taxbinder import llm

        done = 0
        while not self._stop.is_set():
            model = llm.current(self.state.models_dir)
            todo = self._todo(model.id) if model else []
            self.waiting = len(todo)
            if model is None or not todo:
                return done
            store, doc_id = todo[0]
            done += self._read(store, doc_id, model) is not None
        return done

    def read_now(self, store: RecordStore, doc_id: str) -> dict[str, Any]:
        """Read one document with the model now (Add documents): what it found, in
        counts ({"state": "no model" | "not needed" | "failed" | "read", …})."""
        from taxbinder import llm

        model = llm.current(self.state.models_dir)
        if model is None:
            return {"state": "no model"}
        try:
            result = self._read(store, doc_id, model)
        except Exception as e:  # noqa: BLE001 - it's tried again in the background
            log.warning("reading with the model failed (%s)", type(e).__name__)
            return {"state": "failed"}
        if result is None:
            return {"state": "not needed"}
        boxes = [b for f in result["forms"].values() for b in f["boxes"].values()]
        return {
            "state": "read",
            "boxes": len(boxes),
            "agreed": sum(b["status"] == "confirmed" for b in boxes),
            "differ": sum(b["status"] == "disagree" for b in boxes),
            "seconds": round(sum(f.get("seconds", 0) for f in result["forms"].values())),
        }

    def _read(self, store: RecordStore, doc_id: str, model: Any) -> dict[str, Any] | None:  # noqa: ANN401 - an llm.Model
        """Read one document and keep what the model read; None if it isn't wanted (any more)."""
        from taxbinder import matrix
        from taxbinder.extract import crosscheck
        from taxbinder.ingest.inbox import scanned_pages, use_model_tin

        def here() -> bool:
            return store in (self.state.store, *self.state._entity_stores.values())

        with self._busy:
            with self.state.lock:
                if not here():
                    return None  # another binder opened meanwhile
                doc, _ = store.get("document", doc_id)
                if not wanted(doc, model.id, store):
                    return None  # a form it doesn't read, or read already
                data = store.blobs.get(doc.sha256)
                scans = scanned_pages(store, doc)
                year = doc.classification.tax_year if doc.classification else None
            with perf.step("model.read"):  # the engine, outside the lock
                result = crosscheck.read(self._engine(model), data, scans, year, doc.mime,
                                         model.id)  # fmt: skip
            with self.state.lock, perf.step("model.keep"):
                if not here():
                    return None
                doc, rev = store.get("document", doc_id)
                with store.operation(ACTOR, f"model: read {doc_id}"):
                    blob = store.blobs.put(json.dumps(result).encode(), "model.json")
                    store.put(doc.model_copy(update={"model_read": blob.sha256}), rev)
                use_model_tin(store, doc_id, ACTOR)  # an EIN the rules missed
                matrix.read_pdfs(store, "auto:matrix", only_docs={doc_id})
        return result
