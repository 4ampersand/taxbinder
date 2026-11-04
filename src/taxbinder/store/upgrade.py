"""Upgrading an older vault's format: a backup first, then each step
from one format to the next, then the new format recorded as one change in history.

Runs when a store opens a vault, with the vault lock held. When a model or the vault layout
changes: raise `FORMAT` in vault.py, add the step here, and add a fixture vault made by the
previous version (tests/fixtures/vaults/); never edit an old fixture.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from taxbinder.store import vault as vault_module
from taxbinder.store.atomic import write_atomic
from taxbinder.yamlio import dump_yaml, load_yaml

if TYPE_CHECKING:
    from taxbinder.store.records import RecordStore

BACKUP_DIR = "Backups before upgrade"  # beside the vault: works without the user's backup drive


def _drop_connector_fields(store: RecordStore) -> None:
    """2 → 3: the fields for automatic downloads, which the app doesn't do (§7.1), go:
    an institution's `portal.connector / credential_ref / mfa` (and `portal` when nothing is
    left) and `notifications`; a document's `acquired.from`. None of them is encrypted, so the
    files are changed as plain YAML; the index is built again from them."""
    from taxbinder.store.kinds import KINDS  # here: kinds imports the models

    records = store.vault.records
    changed: list[str] = []
    for kind, fix in (("institution", _institution), ("document", _document)):
        for path in sorted(records.glob(KINDS[kind].glob(None))):
            data = load_yaml(path.read_text())
            if isinstance(data, dict) and fix(data):
                write_atomic(path, dump_yaml(data).encode())
                changed.append(str(path.relative_to(records)))
    if changed:
        message = "upgrade: download connectors' fields removed"
        store.vault.history.commit(changed, "taxbinder", message)
        store.reindex()


def _pop(data: object, key: str) -> bool:
    """Remove `key` from a mapping; whether it was there (even as null)."""
    if isinstance(data, dict) and key in data:
        del data[key]
        return True
    return False


def _institution(data: dict) -> bool:
    dropped = _pop(data, "notifications")
    portal = data.get("portal")
    gone = [_pop(portal, key) for key in ("connector", "credential_ref", "mfa")]
    if any(gone) and isinstance(portal, dict) and all(v is None for v in portal.values()):
        del data["portal"]  # it held only the connector's fields
    return dropped or any(gone)


def _document(data: dict) -> bool:
    copies = [data.get("acquired"), *(data.get("reacquired") or [])]
    return any([_pop(c, "from") for c in copies])  # a list: every copy, not the first only


# Format n → the step that turns a format-n vault into format n+1.
STEPS: dict[int, Callable[[RecordStore], None]] = {2: _drop_connector_fields}


class UpgradeFailed(RuntimeError):
    """A step failed; the vault keeps its old format and the backup is named."""


def upgrade(store: RecordStore) -> None:
    vault = store.vault
    if not vault.info_file.exists():
        return
    info = load_yaml(vault.info_file.read_text())
    current, target = info["format"], vault_module.FORMAT
    if current >= target:
        return
    from taxbinder import backup  # here, not at the top: backup imports the store

    made = backup.create(vault, vault.root.parent / BACKUP_DIR, remember=False)
    try:
        for n in range(current, target):
            STEPS[n](store)
    except Exception as e:
        raise UpgradeFailed(
            f"Updating this vault for the new version of TaxBinder failed: {e}. The vault keeps "
            f"its old format, and a backup from just before is in {made.path}."
        ) from e
    info["format"] = target
    write_atomic(vault.info_file, dump_yaml(info).encode())
    vault.history.commit(["vault.yaml"], "taxbinder", f"upgrade vault format {current} to {target}")
