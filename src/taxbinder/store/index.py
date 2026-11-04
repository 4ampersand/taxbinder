"""Derived SQLite index of records (§4.1, §4.7). Rebuildable at any time from the files."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    kind TEXT NOT NULL,
    id   TEXT NOT NULL,
    year INTEGER,
    path TEXT NOT NULL,
    rev  TEXT NOT NULL,
    data TEXT NOT NULL,  -- on-disk form of the record, as JSON
    PRIMARY KEY (kind, id)
)
"""


class Index:
    def __init__(self, path: Path) -> None:
        # Used from request and watcher threads; callers serialise access (AppState.lock).
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(SCHEMA)

    def upsert(
        self, kind: str, id: str, year: int | None, path: str, rev: str, data: dict[str, Any]
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (kind, id, year, path, rev, json.dumps(data, default=str, sort_keys=True)),
            )

    def delete(self, kind: str, id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, id))

    def clear(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM records")

    def rows(self) -> list[tuple[Any, ...]]:
        return self.db.execute("SELECT * FROM records ORDER BY kind, id").fetchall()

    def close(self) -> None:
        self.db.close()
