"""The performance log: where the time goes, to find what makes the app slow.

A file of its own, `perf.log`, apart from the other logs: one JSON line per event, each with
the app's version so versions can be compared. Events: a page or API request (its route, never
its address: no ids or query strings), a named step inside one (`step`), and the store's lock
waited for or held long. Nothing personal: names of routes, steps and threads, and times.

    {"at": "...", "v": "0.10.0", "kind": "request", "req": 12, "route": "/", "ms": 840.2, ...}
    {"at": "...", "v": "0.10.0", "kind": "step", "req": 12, "name": "check.report", "ms": 610.0}
    {"at": "...", "v": "0.10.0", "kind": "lock-wait", "who": "/check", "ms": 2300.4,
     "held_by": "model-reader"}

`TAXBINDER_PERF_LOG` names the file (tests, development); otherwise ~/Library/Logs/TaxBinder.
"""

from __future__ import annotations

import contextvars
import itertools
import json
import logging
import logging.handlers
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("taxbinder.perf")
log.propagate = False  # never mixed into the other logs

LOCK_WAIT_MS = 50  # a lock wait or hold shorter than this isn't written
LOCK_HELD_MS = 200

# the request being served: its number, route, and its tallies (name → [calls, ms])
_request: contextvars.ContextVar[tuple[int, str, dict[str, list[float]]] | None] = (
    contextvars.ContextVar("perf_request", default=None)
)
_numbers = itertools.count(1)


def default_path() -> Path:
    if path := os.environ.get("TAXBINDER_PERF_LOG"):
        return Path(path).expanduser()
    return Path.home() / "Library" / "Logs" / "TaxBinder" / "perf.log"


def setup(path: Path | None = None) -> Path:
    """Start writing the log (the sidecar, once); 5 files of 5 MB at most."""
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=4)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    write("start", pid=os.getpid())
    return path


def write(kind: str, **fields: Any) -> None:  # noqa: ANN401
    if not log.handlers:
        return  # not set up: tests, the CLI
    from taxbinder import __version__

    at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    log.info(json.dumps({"at": at, "v": __version__, "kind": kind, **fields}))


def who() -> str:
    """The request being served on this thread (its route), else the thread's name."""
    r = _request.get()
    return r[1] if r else threading.current_thread().name


@contextmanager
def request(route: str) -> Iterator[tuple[int, dict[str, list[float]]]]:
    """A request being served: steps and lock waits inside it carry its number; its tallies
    go on its line."""
    n, tallies = next(_numbers), {}
    token = _request.set((n, route, tallies))
    try:
        yield n, tallies
    finally:
        _request.reset(token)


@contextmanager
def tally(name: str) -> Iterator[None]:
    """Work done many times in a request (reading a record, decrypting a file): counted and
    added up on the request's line, not a line each. Outside a request: nothing."""
    r = _request.get()
    if r is None:
        yield
        return
    t = time.perf_counter()
    try:
        yield
    finally:
        c = r[2].setdefault(name, [0, 0.0])
        c[0] += 1
        c[1] += (time.perf_counter() - t) * 1000


def tallies(t: dict[str, list[float]]) -> dict[str, list[float]]:
    return {k: [int(c), round(ms, 1)] for k, (c, ms) in sorted(t.items())}


@contextmanager
def step(name: str) -> Iterator[None]:
    """A named piece of work, timed (inside a request: with its number)."""
    t = time.perf_counter()
    try:
        yield
    finally:
        r = _request.get()
        extra = {"req": r[0], "route": r[1]} if r else {"thread": threading.current_thread().name}
        write("step", name=name, ms=round((time.perf_counter() - t) * 1000, 1), **extra)


class TimedLock:
    """The store's one lock, as `threading.Lock`, writing long waits (and who held it) and long
    holds to the performance log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._since = 0.0
        self._holder = ""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        t = time.perf_counter()
        holder = self._holder
        got = self._lock.acquire(blocking, timeout)
        if got:
            now = time.perf_counter()
            waited = (now - t) * 1000
            self._since, self._holder = now, who()
            if waited >= LOCK_WAIT_MS:
                write("lock-wait", who=self._holder, ms=round(waited, 1), held_by=holder)
        return got

    def release(self) -> None:
        held = (time.perf_counter() - self._since) * 1000
        holder = self._holder
        self._lock.release()
        if held >= LOCK_HELD_MS:
            write("lock-held", who=holder, ms=round(held, 1))

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def timed(name: str, many: bool = False):  # noqa: ANN201
    """A function timed as a step, or (`many`: called many times a request) tallied."""
    import functools

    def wrap(f):  # noqa: ANN001, ANN202
        @functools.wraps(f)
        def inner(*a, **k):  # noqa: ANN002, ANN003, ANN202
            with tally(name) if many else step(name):
                return f(*a, **k)

        return inner

    return wrap
