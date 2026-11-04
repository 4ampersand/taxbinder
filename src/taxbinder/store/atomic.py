"""Crash-safe file writes (§4.1): temp file in the same directory, fsync, rename."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

TMP_SUFFIX = ".tmp"


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data`; a crash leaves either the old or the new file, never half."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
