"""Audit trail: `records/` is a git repository; each store operation is one commit (§4.1).

Uses dulwich, so no git binary is needed. Only metadata lives here: blobs sit beside `records/`
and identifier fields are already encrypted when files are written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

COMMITTER = b"TaxBinder <taxbinder@localhost>"


class History:
    def __init__(self, records: Path) -> None:
        self.records = records

    @classmethod
    def create(cls, records: Path) -> History:
        porcelain.init(str(records))
        return cls(records)

    def commit(self, paths: list[str], actor: str, message: str) -> bytes:
        """Commit the given paths (relative to records/) as one change by `actor`."""
        email = re.sub(r"[^\w.-]", "-", actor.lower()) + "@taxbinder.local"
        with Repo(str(self.records)) as repo:
            present = [str(self.records / p) for p in paths if (self.records / p).exists()]
            gone = [str(self.records / p) for p in paths if not (self.records / p).exists()]
            if present:
                porcelain.add(repo, paths=present)
            if gone:  # deleted records: drop them from the next commit's tree
                porcelain.remove(repo, paths=gone, cached=True)
            return porcelain.commit(
                repo,
                message=message.encode(),
                author=f"{actor} <{email}>".encode(),
                committer=COMMITTER,
            )

    def log(self) -> list[tuple[str, str]]:
        """(author, message) per commit, newest first."""
        with Repo(str(self.records)) as repo:
            return [
                (e.commit.author.decode(), e.commit.message.decode().strip())
                for e in repo.get_walker()
            ]

    # -- reading back, for the History page and undo ------------------------------------

    def changes(self, limit: int = 100) -> list[Change]:
        """Recent commits, newest first, with the record files each one touched."""
        out = []
        with Repo(str(self.records)) as repo:
            for entry in repo.get_walker(max_entries=limit):
                c = entry.commit
                paths: set[str] = set()
                for change in entry.changes():
                    for side in (change.old, change.new):
                        if side is not None and side.path:
                            paths.add(side.path.decode())
                out.append(
                    Change(
                        sha=c.id.decode(),
                        author=c.author.decode().split(" <")[0],
                        message=c.message.decode().strip(),
                        time=datetime.fromtimestamp(c.commit_time, UTC),
                        paths=sorted(paths),
                        parent=c.parents[0].decode() if c.parents else None,
                    )
                )
        return out

    def head(self) -> str | None:
        with Repo(str(self.records)) as repo:
            try:
                return repo.head().decode()
            except KeyError:
                return None

    def unsaved(self) -> list[str]:
        """Record files that differ from the last change (left by a change that never
        finished): changed, deleted or new `.yaml` files, relative to records/."""
        st = porcelain.status(str(self.records), untracked_files="all")
        name = lambda p: p.decode() if isinstance(p, bytes) else str(p)  # noqa: E731
        staged = [name(p) for group in st.staged.values() for p in group]
        untracked = [name(p) for p in st.untracked]
        return sorted(
            {*staged, *map(name, st.unstaged), *(p for p in untracked if p.endswith(".yaml"))}
        )

    def put_back(self, paths: list[str]) -> None:
        """Make these record files what they were at the last change (absent if new since),
        in the folder and in git's index (so the next change commits only its own files)."""
        head = self.head()
        present, gone = [], []
        for rel in paths:
            path = self.records / rel
            data = self.file_at(head, rel)
            if data is None:
                path.unlink(missing_ok=True)
                gone.append(rel)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                present.append(str(path))
        with Repo(str(self.records)) as repo:
            if present:
                porcelain.add(repo, paths=present)
            index = repo.open_index()
            for rel in gone:
                if rel.encode() in index:
                    del index[rel.encode()]
            index.write()

    def file_at(self, sha: str | None, path: str) -> bytes | None:
        """A record file's bytes as of a commit (None: absent then, or no commit)."""
        if sha is None:
            return None
        with Repo(str(self.records)) as repo:
            tree = repo[repo[sha.encode()].tree]
            try:
                _, blob = tree.lookup_path(repo.object_store.__getitem__, path.encode())
            except KeyError:
                return None
            return repo[blob].data


@dataclass(frozen=True)
class Change:
    sha: str
    author: str
    message: str
    time: datetime
    paths: list[str]
    parent: str | None

    @property
    def title(self) -> str:
        """The message without an undo's internal reference ("undo: … [1a2b3c4d5e6f]")."""
        return re.sub(r" \[[0-9a-f]{12}\]$", "", self.message)

    @property
    def automatic(self) -> bool:
        """Re-checks and other changes nobody made by hand."""
        return self.message.startswith("reconcile") or self.author.startswith("auto:")
