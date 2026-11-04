"""Copy diagnostic info: what someone pastes into a problem report. Versions,
the binder's format, counts (filers, documents, years, review corrections) and the recent
errors. Never a value or a file name: errors keep only the fixed sentence from the code (the
values given with it are dropped), and a last pass masks anything that looks like an account
number, a path in the home folder or an email address.

Recent errors are kept in memory while the app runs (the last 20); nothing is written to disk."""

from __future__ import annotations

import logging
import platform
import re
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from taxbinder.entities import MOVED
from taxbinder.store import RecordStore
from taxbinder.store.vault import FORMAT, OLDEST
from taxbinder.whose import CHANGED

KEEP = 20
PACKAGE = Path(__file__).resolve().parent  # TaxBinder's own code, wherever it's installed
WATCHED = ("taxbinder", "uvicorn.error")  # the app's own loggers, and the server's crashes


@dataclass
class Logged:
    at: datetime
    level: str
    where: str  # the logger: "taxbinder.ocr"
    text: str  # the fixed sentence from the code
    error: str = ""  # a crash: its kind and the line in TaxBinder's code


class Recent(logging.Handler):
    """The last warnings and errors, without the values given with them."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.items: deque[Logged] = deque(maxlen=KEEP)

    def emit(self, record: logging.LogRecord) -> None:
        text = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
        error = ""
        if record.exc_info and record.exc_info[1] is not None:
            kind = type(record.exc_info[1]).__name__
            frames = traceback.extract_tb(record.exc_info[2])
            ours = [f for f in frames if Path(f.filename).resolve().is_relative_to(PACKAGE)]
            where = ""
            if ours:
                f = ours[-1]
                tail = Path(f.filename).resolve().relative_to(PACKAGE).as_posix()
                where = f" at taxbinder/{tail}:{f.lineno} in {f.name}"
            error = f"{kind}{where}"
        self.items.append(Logged(datetime.fromtimestamp(record.created), record.levelname,
                                 record.name, text, error))  # fmt: skip


RECENT = Recent()


def watch() -> None:
    """Start keeping recent errors (once, when the app starts)."""
    for name in WATCHED:
        logger = logging.getLogger(name)
        if RECENT not in logger.handlers:
            logger.addHandler(RECENT)


@dataclass
class Counts:
    documents: int = 0
    years: int = 0
    read_corrections: int = 0  # type, year or sender changed by the person
    owner_corrections: int = 0  # whose it is changed, or moved to another filer


def counts(stores: list[RecordStore]) -> Counts:
    out, years = Counts(), set()
    for store in stores:
        for d in store.list("document"):
            out.documents += 1
            c = d.classification
            if c and c.tax_year:
                years.add(c.tax_year)
            if c and "human" in (c.method or "").split("+"):
                out.read_corrections += 1
            if CHANGED in d.tags or MOVED in d.tags:
                out.owner_corrections += 1
    out.years = len(years)
    return out


MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "…@…"),  # an email address
    (re.compile(r"(?:/Users|/home|~)/[^\s:)]+"), "…/…"),  # a path in a home folder
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{2}-\d{7}\b"), "…"),  # an SSN or EIN
    (re.compile(r"\b\d{6,}\b"), "…"),  # an account-like number (dates and times stay)
]


def mask(text: str) -> str:
    for pattern, by in MASKS:
        text = pattern.sub(by, text)
    return text


def report(stores: list[RecordStore], formats: list[int], filers: int, sharing: bool,
           now: datetime | None = None) -> str:  # fmt: skip
    """The text to copy. `stores`: every binder that opens; `formats`: their format numbers."""
    c = counts(stores)
    mac = platform.mac_ver()[0] or platform.platform()
    chip = {"arm64": "Apple Silicon", "x86_64": "Intel"}.get(platform.machine(), platform.machine())
    lines = [
        "TaxBinder diagnostic info",
        f"Made: {(now or datetime.now()).strftime('%Y-%m-%d %H:%M')}",
        f"TaxBinder: {version('taxbinder')}",
        f"macOS: {mac} ({chip})",
        f"Binder format: {', '.join(map(str, sorted(set(formats)))) or 'none'} "
        f"(this version opens {f'format {FORMAT}' if OLDEST == FORMAT else f'{OLDEST}–{FORMAT}'})",
        f"Filers: {filers} (the household{f' and {filers - 1} more' if filers > 1 else ''})",
        f"Documents: {c.documents}, over {c.years} year{'s' if c.years != 1 else ''}",
        f"Corrected after reading (type, year or sender): {c.read_corrections}",
        f"Given to another person or filer after reading: {c.owner_corrections}",
        f"Home sharing: {'on' if sharing else 'off'}",
        "",
        f"Recent warnings and errors (since TaxBinder started, last {KEEP}):",
    ]
    items = list(RECENT.items)
    lines += [f"  {i.at:%Y-%m-%d %H:%M:%S} {i.level} {i.where}: {i.text}"
              + (f" ({i.error})" if i.error else "") for i in items] or ["  none"]  # fmt: skip
    return mask("\n".join(lines)) + "\n"


def formats_of(paths: list[Path]) -> list[int]:
    """The binders' format numbers, from their vault.yaml (no key needed)."""
    from taxbinder.store import Vault
    from taxbinder.yamlio import load_yaml

    out = []
    for p in paths:
        try:
            out.append(int(load_yaml(Vault(p).info_file.read_text())["format"]))
        except Exception:  # noqa: BLE001 - a binder that isn't there or is damaged
            continue
    return out
