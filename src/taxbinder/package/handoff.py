"""Files for your preparer: the package as one .zip in a folder (the app window's
Downloads), and whether the last one is out of date: a document for the year arrived after it
was made (a new one, or a corrected version). Making it is remembered; it never counts as sent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from taxbinder.downloads import unique_path
from taxbinder.models import YearConfig
from taxbinder.models.year import PackageMade
from taxbinder.package.build import PackageResult, build_zip, document_ids, zip_name
from taxbinder.store import RecordNotFound, RecordStore


@dataclass
class Made:
    at: datetime
    documents: int  # in the .zip
    new: int  # the year's documents not in it


def _config(store: RecordStore, year: int) -> tuple[YearConfig, str | None]:
    try:
        return store.get("year", str(year))
    except RecordNotFound:
        return YearConfig(year=year), None


def last(store: RecordStore, year: int) -> Made | None:
    """The last .zip made for the year, and how many documents arrived since."""
    made = _config(store, year)[0].package
    if made is None:
        return None
    new = set(document_ids(store, year)) - set(made.documents)
    return Made(made.made_at, len(made.documents), len(new))


def make(
    store: RecordStore, year: int, folder: Path, actor: str, now: datetime | None = None
) -> tuple[Path, PackageResult]:
    """The .zip into `folder` (never overwriting: " (2)"…), remembered for the year."""
    now = now or datetime.now(UTC)
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder, zip_name(year))
    with store.operation(actor, f"files for your preparer {year}: made the .zip"):
        result = build_zip(store, year, target, actor, now)  # its checks run first
        config, rev = _config(store, year)
        made = PackageMade(made_at=now, documents=document_ids(store, year))
        store.put(config.model_copy(update={"package": made}), rev)
    return target, result
