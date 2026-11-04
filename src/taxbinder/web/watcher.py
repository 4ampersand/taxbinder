"""Inbox watcher: ingest settled files, then reconcile, so the dashboard stays current."""

from __future__ import annotations

import logging
import threading

from taxbinder.ingest.inbox import Ingested, scan_inbox
from taxbinder.rules.reconcile import reconcile
from taxbinder.web.app import AppState

log = logging.getLogger("taxbinder.watcher")


def process_inbox(state: AppState) -> list[Ingested]:
    """Files in the household's inbox (where portal windows save) go into the entity the app
    window is working on, and wait for Check."""
    with state.locked_store() as store:
        if store is None:
            return []
        results = scan_inbox(store, inbox=state.vault_path / "inbox", waiting=True)
        if results:
            years = {state.today().year - 1}
            for r in results:
                doc, _ = store.get("document", r.doc_id)
                if doc.classification and doc.classification.tax_year:
                    years.add(doc.classification.tax_year)
            for year in sorted(years):
                try:
                    reconcile(store, year, "auto:ingest")
                except ValueError:  # a past year without rule parameters: nothing to check
                    pass
            from taxbinder import matrix

            new = {r.doc_id for r in results if r.linked_to and not r.duplicate}
            if new:
                matrix.read_pdfs(store, "auto:matrix", only_docs=new)  # suggestions, never values
            linked = sum(1 for r in results if r.linked_to)
            again = sum(1 for r in results if r.duplicate)
            log.info(
                "ingested %d file(s): %d new (%d matched), %d already in the vault",
                len(results),
                len(results) - again,
                linked,
                again,
            )
    return results


class InboxWatcher(threading.Thread):
    """Polls the inbox (polling behaves the same everywhere; FS events don't)."""

    def __init__(self, state: AppState, interval: float = 1.0) -> None:
        super().__init__(name="inbox-watcher", daemon=True)
        self.state = state
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                process_inbox(self.state)
            except Exception:  # noqa: BLE001 - keep watching; the file stays for a retry
                log.exception("inbox scan failed")

    def stop(self) -> None:
        self._stop.set()
