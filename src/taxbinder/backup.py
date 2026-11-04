"""Backups: the household's vault and every entity's, as one encrypted file (`.tbak`).

The archive is encrypted to the vault's own recipients (this Mac's key and the printed recovery
key), so it opens only with one of them; nothing in it is readable without a key, not even
amounts or names. The search index is left out (it's rebuilt), and so is the lock file.
A restore goes into a new folder, never over a vault in use.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pyrage import decrypt_io, encrypt_io, x25519

from taxbinder.entities import HOUSEHOLD, list_entities
from taxbinder.store import Vault

SUFFIX = ".tbak"
FORMAT = 1
MANIFEST = "taxbinder-backup.json"
SKIP = {"index.sqlite", "index.sqlite-wal", "index.sqlite-shm", ".lock"}


@dataclass
class Made:
    path: Path
    size: int
    vaults: int
    files: int


def _roots(household: Path) -> list[tuple[str, Path]]:
    """(name in the archive, folder) for the household's vault and every entity's."""
    return [
        ("vault" if ref.slug == HOUSEHOLD else f"entities/{ref.slug}/vault", ref.path)
        for ref in list_entities(household)
        if ref.path.is_dir()
    ]


def create(vault: Vault, out_dir: Path, now: datetime | None = None, remember: bool = True) -> Made:
    """Write `TaxBinder backup <date>.tbak` into `out_dir` (never overwriting). Hold the vault
    lock while this runs, so no change lands halfway. `remember=False` (the backup before an
    upgrade) leaves "last backup" as it was."""
    if vault.crypto is None:
        raise ValueError("this vault isn't encrypted; refusing to make an unencrypted backup")
    now = now or datetime.now(UTC)
    roots = _roots(vault.root)
    files = 0
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as raw:
        with tarfile.open(fileobj=raw, mode="w:gz") as tar:
            manifest = {
                "format": FORMAT,
                "created": now.isoformat(),
                "vaults": [name for name, _ in roots],
            }
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mtime = len(data), int(now.timestamp())
            tar.addfile(info, io.BytesIO(data))
            for name, root in roots:
                for path in sorted(root.rglob("*")):
                    if path.name in SKIP or not (path.is_file() or path.is_dir()):
                        continue
                    if path.is_file():
                        files += 1
                    tar.add(path, arcname=f"{name}/{path.relative_to(root)}", recursive=False)
        raw.seek(0)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = _free(out_dir / f"TaxBinder backup {now.astimezone():%Y-%m-%d}{SUFFIX}")
        partial = target.with_name(target.name + ".partial")
        with partial.open("wb") as out:
            encrypt_io(raw, out, list(vault.crypto.recipients))
        partial.replace(target)
    if remember:
        record(vault.root, target, now)
    return Made(target, target.stat().st_size, len(roots), files)


def _free(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate, n = path.with_name(f"{path.stem} ({n}){path.suffix}"), n + 1
    return candidate


def _status_file(household: Path) -> Path:
    return household.parent / "last-backup.json"  # beside the vault, not in its history


def record(household: Path, path: Path, when: datetime) -> None:
    _status_file(household).write_text(
        json.dumps({"at": when.isoformat(), "name": path.name, "size": path.stat().st_size})
    )


def last(household: Path) -> dict | None:
    """When the last backup was made from this Mac (and its file name), if ever."""
    try:
        return json.loads(_status_file(household).read_text())
    except (OSError, ValueError):
        return None


def restore(backup: Path, secret: str, target: Path) -> dict:
    """Unpack a backup into `target` (a new or empty folder) with the recovery key or this Mac's
    key. Returns its manifest. The vaults land in `target/vault` and `target/entities/…`."""
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"{target} isn't empty; restore into a new folder")
    try:
        identity = x25519.Identity.from_str(secret.strip())
    except Exception as e:  # noqa: BLE001 - malformed key
        raise ValueError("that isn't a TaxBinder key (it starts with AGE-SECRET-KEY-)") from e
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as raw:
        with backup.open("rb") as src:
            try:
                decrypt_io(src, raw, [identity])
            except Exception as e:  # noqa: BLE001 - pyrage.DecryptError
                raise ValueError("that key doesn't open this backup") from e
        raw.seek(0)
        target.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=raw, mode="r:gz") as tar:
                manifest = json.loads(tar.extractfile(MANIFEST).read())  # type: ignore[union-attr]
                if manifest.get("format") != FORMAT:
                    raise ValueError("this backup was made by a newer TaxBinder")
                tar.extractall(target, filter="data")  # no paths outside target, no devices
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    return manifest
