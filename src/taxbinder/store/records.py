"""RecordStore: the single writer for vault records (§4.1 write discipline).

- One store per vault at a time (fcntl lock).
- Atomic writes; `rev` (SHA-256 of the file) for optimistic concurrency.
- Writes happen inside `operation(actor, message)`; each operation is one git commit.
- Identifier fields are encrypted on the way to disk (the vault's field codec).
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from taxbinder import perf
from taxbinder.store.atomic import write_atomic
from taxbinder.store.blobs import BlobStore
from taxbinder.store.codec import FieldCodec, PlainFieldCodec
from taxbinder.store.index import Index
from taxbinder.store.kinds import KINDS, Kind, kind_of, record_id, record_year
from taxbinder.store.vault import Vault
from taxbinder.yamlio import dump_yaml, load_yaml, to_data

log = logging.getLogger("taxbinder.store")


class VaultLocked(Exception):
    """Another RecordStore (in this or another process) holds the vault."""


class RevConflict(Exception):
    """The record changed since `expected_rev` was read (or already exists on create)."""

    def __init__(self, kind: str, id: str, current_rev: str | None) -> None:
        super().__init__(f"{kind} {id!r} changed: current rev is {current_rev}")
        self.current_rev = current_rev


class RecordNotFound(KeyError):
    pass


class NoOperation(RuntimeError):
    """Writes must happen inside `store.operation(actor, message)`."""


@dataclass
class Operation:
    actor: str
    message: str
    paths: list[str] = field(default_factory=list)
    sha: str | None = None  # the commit, once written (encrypted vaults only)


def file_rev(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RecordStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.codec: FieldCodec = vault.crypto.fields if vault.crypto else PlainFieldCodec()
        self.blobs = BlobStore(vault.blobs, vault.crypto.blobs if vault.crypto else None)
        # each record file read once (a page can read the same records dozens of times):
        # path → (the file as it was then: inode, mtime, size), (record, rev). A write
        # replaces the file, so a changed one is read again, whoever changed it.
        self._read: dict[Path, tuple[tuple[int, int, int], tuple[Any, str]]] = {}
        self._lock_fd = os.open(vault.lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            raise VaultLocked(f"vault is in use: {vault.root}") from None
        self._index = Index(vault.index)
        self._op: Operation | None = None
        self.completed: list[Operation] = []
        from taxbinder.store.upgrade import upgrade  # here: it imports this module

        try:
            if vault.crypto and (left := vault.history.unsaved()):
                log.warning("putting back %d record file(s) a change left unfinished", len(left))
                self._put_back(left)  # the app stopped during a change: as it was before
            upgrade(self)  # an older vault's format, with the lock held
        except BaseException:
            self.close()
            raise

    def _put_back(self, paths: list[str]) -> None:
        """Undo a change that didn't finish: its files as at the last change, and the index."""
        if not self.vault.crypto:  # a bare test vault keeps no history to go back to
            return
        self.vault.history.put_back(sorted(set(paths)))
        self.reindex()

    # -- lifecycle -------------------------------------------------------------------------

    def close(self) -> None:
        if self._lock_fd >= 0:
            self._index.close()
            os.close(self._lock_fd)  # releases the flock
            self._lock_fd = -1

    def __enter__(self) -> RecordStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- paths -----------------------------------------------------------------------------

    def _path(self, kind: Kind, id: str) -> Path:
        try:
            kind.check_id(id)
        except ValidationError:
            raise ValueError(f"invalid {kind.name} id {id!r}") from None
        return (self.vault.records / kind.path(id)).resolve()

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.vault.records.resolve()))

    # -- reads -----------------------------------------------------------------------------

    def _load(self, kind: Kind, raw: bytes) -> Any:
        return kind.adapter.validate_python(self.codec.decode(load_yaml(raw.decode())))

    def _cached(self, k: Kind, path: Path) -> tuple[Any, str]:
        """A record file's record and rev: read once, again only when the file has changed.
        Each caller gets a copy of its own."""
        st = path.stat()  # FileNotFoundError when it's gone
        mark = (st.st_ino, st.st_mtime_ns, st.st_size)
        hit = self._read.get(path)
        if hit is None or hit[0] != mark:
            raw = path.read_bytes()
            hit = (mark, (self._load(k, raw), file_rev(raw)))
            self._read[path] = hit
        record, rev = hit[1]
        return record.model_copy(), rev

    def get(self, kind: str, id: str) -> tuple[Any, str]:
        """Return `(record, rev)`."""
        k = KINDS[kind]
        path = self._path(k, id)
        with perf.tally(f"store.get:{kind}"):
            try:
                return self._cached(k, path)
            except FileNotFoundError:
                raise RecordNotFound(f"{kind} {id!r}") from None

    def list(self, kind: str, year: int | None = None) -> list[Any]:
        k = KINDS[kind]
        with perf.tally(f"store.list:{kind}"):
            out = []
            for p in sorted(self.vault.records.glob(k.glob(year))):
                try:
                    out.append(self._cached(k, p)[0])
                except FileNotFoundError:
                    continue  # removed meanwhile
            return out

    # -- writes ----------------------------------------------------------------------------

    @contextmanager
    def operation(self, actor: str, message: str) -> Iterator[Operation]:
        """Group writes into one logical operation (one git commit); one started
        inside another joins it. All or nothing: if it fails, what it wrote is put back."""
        if self._op is not None:  # part of a larger change (Check's filing): it joins that one
            yield self._op
            return
        self._op = Operation(actor, message)
        try:
            yield self._op
        except BaseException:  # all or nothing: what it wrote is put back as it was
            op, self._op = self._op, None
            if op.paths:
                self._put_back(op.paths)
            raise
        op, self._op = self._op, None
        if op.paths:
            if self.vault.crypto:  # encrypted vaults keep history; bare test vaults don't
                op.sha = self.vault.history.commit(op.paths, op.actor, op.message).decode()
            self.completed.append(op)

    def put(self, record: BaseModel, expected_rev: str | None) -> str:
        """Create (`expected_rev=None`) or update a record; returns its new rev."""
        if self._op is None:
            raise NoOperation("put() outside store.operation(actor, message)")
        k = kind_of(record)
        id = record_id(record)
        k.adapter.validate_python(to_data(record))  # never write an invalid record
        path = self._path(k, id)

        previous = path.read_bytes() if path.exists() else None
        current_rev = file_rev(previous) if previous is not None else None
        if current_rev != expected_rev:
            raise RevConflict(k.name, id, current_rev)

        on_disk = self.codec.encode(
            to_data(record), load_yaml(previous.decode()) if previous is not None else None
        )
        raw = dump_yaml(on_disk).encode()
        rev = file_rev(raw)
        if raw != previous:
            write_atomic(path, raw)
            self._op.paths.append(self._rel(path))
        self._index.upsert(k.name, id, record_year(record), self._rel(path), rev, on_disk)
        return rev

    def delete(self, kind: str, id: str, expected_rev: str) -> None:
        """Remove a record; the deletion is one more change in the vault history."""
        if self._op is None:
            raise NoOperation("delete() outside store.operation(actor, message)")
        k = KINDS[kind]
        path = self._path(k, id)
        try:
            current_rev = file_rev(path.read_bytes())
        except FileNotFoundError:
            raise RecordNotFound(f"{kind} {id!r}") from None
        if current_rev != expected_rev:
            raise RevConflict(k.name, id, current_rev)
        path.unlink()
        self._read.pop(path, None)
        self._op.paths.append(self._rel(path))
        self._index.delete(k.name, id)

    # -- undo --------------------------------------------------------------------------------

    def undo(self, sha: str, actor: str) -> set[int]:
        """Put back the record files one change touched, as they were just before it; the undo is
        itself a change (so it can be undone). Refused when a later change made by hand touched
        the same records; later automatic re-checks don't count (they run again afterwards).
        Returns the tax years affected."""
        history = self.vault.history
        recent = history.changes(limit=500)
        index = next((i for i, c in enumerate(recent) if c.sha == sha), None)
        if index is None:
            raise ValueError("that change isn't in the recent history")
        target = recent[index]
        if target.parent is None:
            raise ValueError("the vault's creation can't be undone")
        if target.automatic:
            raise ValueError("automatic re-checks can't be undone; they run again by themselves")
        paths = [p for p in target.paths if p != "vault.yaml" and not p.endswith("findings.yaml")]
        if not paths:
            raise ValueError("that change has nothing to undo")
        for later in live_changes(recent[:index]):
            if set(paths) & set(later.paths):
                raise ValueError(
                    f"those records were changed again since (“{later.message}” by "
                    f"{later.author}); undo that first"
                )
        years: set[int] = set()
        with self.operation(actor, f"undo: {target.message} [{target.sha[:12]}]"):
            for rel in paths:
                years |= self._restore(rel, history.file_at(target.parent, rel))
        return years

    def _restore(self, rel: str, data: bytes | None) -> set[int]:
        """Write back (or remove) one record file exactly as stored before; keeps the index."""
        if self._op is None:
            raise NoOperation("restore outside store.operation(actor, message)")
        k = next((k for k in KINDS.values() if fnmatch.fnmatchcase(rel, k.glob(None))), None)
        if k is None:
            raise ValueError(f"not a record file: {rel}")
        path = (self.vault.records / rel).resolve()
        years: set[int] = set()
        if path.exists():
            current = self._load(k, path.read_bytes())
            years.add(record_year(current) or 0)
            if data is None:
                path.unlink()
                self._index.delete(k.name, record_id(current))
        if data is not None:
            record = self._load(k, data)  # never write back something that doesn't validate
            write_atomic(path, data)
            self._index.upsert(
                k.name,
                record_id(record),
                record_year(record),
                rel,
                file_rev(data),
                load_yaml(data.decode()),
            )
            years.add(record_year(record) or 0)
        self._op.paths.append(rel)
        return {y for y in years if y}

    # -- index -----------------------------------------------------------------------------

    def reindex(self) -> int:
        """Rebuild index.sqlite from the record files; returns the number of records."""
        self._index.clear()
        count = 0
        for k in KINDS.values():
            for path in sorted(self.vault.records.glob(k.glob(None))):
                raw = path.read_bytes()
                record = self._load(k, raw)
                self._index.upsert(
                    k.name,
                    record_id(record),
                    record_year(record),
                    self._rel(path.resolve()),
                    file_rev(raw),
                    load_yaml(raw.decode()),
                )
                count += 1
        return count

    def index_rows(self) -> list[tuple[Any, ...]]:
        return self._index.rows()


UNDO_REF = re.compile(r"\[([0-9a-f]{12})\]$")


def live_changes(later: list) -> list:  # noqa: ANN001
    """Of the changes made after the one being undone, those that still stand: automatic
    re-checks are left out, and so is any change undone among them together with its undo (they
    cancel). An undo of something older still counts: it really changed those records."""

    def ref(c) -> str | None:  # noqa: ANN001
        m = UNDO_REF.search(c.message)
        return m.group(1) if m else None

    shas = {c.sha[:12] for c in later}
    undone = {r for c in later if (r := ref(c)) and r in shas}
    return [
        c for c in later if not c.automatic and c.sha[:12] not in undone and ref(c) not in undone
    ]
