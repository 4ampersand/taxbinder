"""Start: open the vault or say why not, create it, put its key back from the
recovery key, or restore a backup into place. All from the app, no Terminal."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Literal

from taxbinder import backup
from taxbinder.entities import entities_dir, list_entities
from taxbinder.store import (
    RecordStore,
    VaultKeyMissing,
    VaultLocked,
    WrongKey,
    init_vault,
    keys_from_env,
    open_vault,
    restore_key,
)
from taxbinder.store.vault import Vault
from taxbinder.yamlio import load_yaml

Kind = Literal["new", "nokey", "other"]
BUSY = (
    "Your binder is open in another copy of TaxBinder, or something else on this Mac is "
    "using it. Close that, then choose Try again."
)


def open_store(path: Path) -> tuple[RecordStore | None, str | None, Kind | None]:
    """The vault's store, or why it didn't open: no vault yet ("new"), no key on this Mac or the
    wrong one ("nokey"), or something else (in use, made by a newer version)."""
    try:
        return RecordStore(open_vault(path, keys_from_env())), None, None
    except FileNotFoundError:
        return None, "There is no binder on this Mac yet.", "new"
    except VaultKeyMissing:
        return None, "This Mac doesn't have the key to your binder.", "nokey"
    except WrongKey:
        return None, "The key on this Mac doesn't open your binder.", "nokey"
    except VaultLocked:
        return None, BUSY, "other"
    except RuntimeError as e:  # VaultTooNew, an old unencrypted development vault
        return None, str(e), "other"


def create(path: Path) -> None:
    """A new, empty vault; its key goes into the Keychain. The recovery key waits there too
    (no weaker than the vault key beside it) until Keep it safe shows it once."""
    keys = keys_from_env()
    vault, recovery = init_vault(path, keys)
    keys.set(pending_recovery_id(vault), recovery)


def pending_recovery_id(vault: Vault) -> str:
    """The Keychain entry holding a recovery key not yet shown to the person."""
    return f"{load_yaml(vault.info_file.read_text())['id']}:recovery-pending"


def unlock(path: Path, recovery: str) -> int:
    """Put the key back from the recovery key, for the household and every entity. Returns how
    many vaults. FileNotFoundError when there's no vault (a key alone brings nothing back);
    WrongKey changes nothing."""
    if not Vault(path.expanduser()).info_file.exists():
        raise FileNotFoundError(path)
    refs = list_entities(path)
    keys = keys_from_env()
    restore_key(refs[0].path, keys, recovery)  # the household first: a wrong key stops here
    for ref in refs[1:]:
        restore_key(ref.path, keys, recovery)
    return len(refs)


def restore(file: Path, secret: str, path: Path) -> dict:
    """Unpack a backup into place where there's no vault yet: the household's vault, every
    entity's beside it, and the key into the Keychain. A wrong key, a damaged file or a full
    disk leaves things as they were. Returns the backup's manifest."""
    path = path.expanduser()
    entities = entities_dir(path)
    if Vault(path).info_file.exists() or (entities.is_dir() and any(entities.iterdir())):
        raise FileExistsError("there is a binder on this Mac already")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".restoring-{uuid.uuid4().hex[:8]}"
    try:
        manifest = backup.restore(file, secret, tmp)
        if not Vault(tmp / "vault").info_file.exists():
            raise ValueError("this backup has no binder in it")
        _move_into(tmp / "vault", path)  # the app may have made the empty inbox already
        if (tmp / "entities").is_dir():
            _move_into(tmp / "entities", entities)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    unlock(path, secret)
    return manifest


def _move_into(src: Path, dst: Path) -> None:
    """Move src's contents into dst (renames, same disk); a file already in dst is kept."""
    dst.mkdir(mode=0o700, parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if not target.exists():
            child.rename(target)
        elif child.is_dir() and target.is_dir():
            _move_into(child, target)
