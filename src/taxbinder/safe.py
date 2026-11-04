"""Keep it safe: the recovery key shown once and confirmed, and a first backup.

The recovery key of a binder made by Get started waits in the Keychain (`start.create`) until
it's shown here and its last 4 characters are typed back; then it's removed for good. A binder
made by `taxbinder init` printed its key then, so it counts as saved. "Remind me later" is kept
beside the vault (like "Last backup"), not in its history."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from taxbinder import backup
from taxbinder.start import pending_recovery_id
from taxbinder.store import keys_from_env, vault_key
from taxbinder.store.vault import Vault

LATER = timedelta(days=7)


def pending(household: Path) -> str | None:
    """The recovery key not yet saved by the person, if one waits (never logged or printed)."""
    vault = Vault(household.expanduser())
    if not vault.info_file.exists():
        return None
    return keys_from_env().get(pending_recovery_id(vault))


def saved(household: Path) -> bool:
    return pending(household) is None


def confirm(household: Path, last4: str) -> bool:
    """The person typed the key's last 4 characters: it's saved, and removed from the Keychain.
    Wrong ones change nothing."""
    key = pending(household)
    typed = last4.strip().upper()
    if key is None or len(typed) != 4 or key[-4:].upper() != typed:
        return False
    keys_from_env().delete(pending_recovery_id(Vault(household.expanduser())))
    return True


def this_macs_key(household: Path) -> str:
    """This Mac's key (as `taxbinder key show`): it opens the binder and its backups alike."""
    return vault_key(household, keys_from_env())


def done(household: Path) -> bool:
    return saved(household) and backup.last(household) is not None


def _later_file(household: Path) -> Path:
    return household.parent / "keep-safe.json"  # beside the vault, not in its history


def remind_later(household: Path, today: date) -> None:
    _later_file(household).write_text(json.dumps({"later": today.isoformat()}))


def reminded(household: Path, today: date) -> bool:
    """Put off by "Remind me later" in the last 7 days (the app doesn't open on it then)."""
    try:
        when = date.fromisoformat(json.loads(_later_file(household).read_text())["later"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return today - when < LATER
