"""The performance log: a file of its own, one JSON line per request, step
and long lock wait, each with the app's version; routes, never addresses."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from test_web import client, state  # noqa: F401 - the test household's app

from taxbinder import __version__, perf
from taxbinder.web.app import AppState


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_requests_steps_and_lock_waits_go_to_their_own_file(
    state: AppState,  # noqa: F811
    tmp_path: Path,
) -> None:
    path = perf.setup(tmp_path / "logs" / "perf.log")
    try:
        c = client(state)
        assert c.get("/").status_code == 200
        doc = "doc_0123456789ab"  # an address with an id: logged by its route
        c.get(f"/documents/{doc}/view")
        lines = _lines(path)
        assert lines[0]["kind"] == "start" and all(x["v"] == __version__ for x in lines)
        page = next(x for x in lines if x["kind"] == "request" and x["route"] == "/")
        assert page["status"] == 200 and page["ms"] > 0
        assert page["tallies"]["store.list:document"][0] >= 1  # calls, ms
        steps = {x["name"] for x in lines if x["kind"] == "step" and x["req"] == page["req"]}
        assert {"page.bar", "journey.steps", "binder.build", "page.render:home.html"} <= steps
        view = next(x for x in lines if x["kind"] == "request" and "view" in x["route"])
        assert view["route"] == "/documents/{doc_id}/view" and doc not in path.read_text()

        def hold() -> None:  # a background thread holding the store a while
            with state.lock:
                time.sleep(0.3)

        t = threading.Thread(target=hold, name="model-reader")
        t.start()
        time.sleep(0.05)
        c.get("/documents")
        t.join()
        lines = _lines(path)
        wait = next(x for x in lines if x["kind"] == "lock-wait")
        assert wait["who"] == "/documents" and wait["held_by"] == "model-reader"
        assert wait["ms"] >= perf.LOCK_WAIT_MS
        held = [x for x in lines if x["kind"] == "lock-held" and x["who"] == "model-reader"]
        assert held and held[0]["ms"] >= 250
        assert not logging.getLogger("taxbinder.perf").propagate  # not in the other logs
    finally:
        for h in list(perf.log.handlers):
            perf.log.removeHandler(h)
            h.close()
