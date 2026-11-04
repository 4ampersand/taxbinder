"""Content-addressed, immutable blob storage (§4.5); encrypted through the codec."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from taxbinder import perf
from taxbinder.store.atomic import TMP_SUFFIX, write_atomic
from taxbinder.store.codec import BlobCodec, PlainBlobCodec


class BlobCorrupt(Exception):
    pass


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    path: str  # relative to blobs/, as recorded in Document.blob


class BlobStore:
    def __init__(self, root: Path, codec: BlobCodec | None = None) -> None:
        self.root = root
        self.codec = codec or PlainBlobCodec()

    def _dir(self, sha: str) -> Path:
        return self.root / sha[:2] / sha[2:4]

    def find(self, sha: str) -> Path | None:
        matches = [p for p in self._dir(sha).glob(f"{sha}*") if not p.name.endswith(TMP_SUFFIX)]
        return matches[0] if matches else None

    def put(self, data: bytes, ext: str = "") -> StoredBlob:
        """Store `data` once; identical content returns the existing blob."""
        sha = hashlib.sha256(data).hexdigest()
        path = self.find(sha)
        if path is None:
            name = sha + (f".{ext.lstrip('.')}" if ext else "") + self.codec.suffix
            path = self._dir(sha) / name
            write_atomic(path, self.codec.encode(data))
            path.chmod(0o400)  # never modified again
        return StoredBlob(sha, str(path.relative_to(self.root)))

    def get(self, sha: str) -> bytes:
        path = self.find(sha)
        if path is None:
            raise FileNotFoundError(f"no blob {sha}")
        with perf.tally("blob.get"):
            return self._read_verified(path, sha)

    def _read_verified(self, path: Path, sha: str) -> bytes:
        try:
            data = self.codec.decode(path.read_bytes())
        except Exception as e:  # noqa: BLE001 - age authentication fails on any damage
            raise BlobCorrupt(f"blob {path.relative_to(self.root)} can't be decrypted") from e
        if hashlib.sha256(data).hexdigest() != sha:
            raise BlobCorrupt(f"blob {path.relative_to(self.root)} does not match its hash")
        return data

    def verify_all(self) -> Iterator[str]:
        """Yield a problem description for every blob whose content doesn't match its name."""
        for path in sorted(self.root.glob("*/*/*")):
            if path.name.endswith(TMP_SUFFIX):
                continue
            try:
                self._read_verified(path, path.name.split(".", 1)[0])
            except Exception as e:  # noqa: BLE001 - corrupt, undecryptable or unreadable
                yield f"blob {path.relative_to(self.root)}: {e}"
