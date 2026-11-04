"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path

from taxbinder.store import Vault, init_vault, keys_from_env


def new_vault(path: Path) -> Vault:
    """An encrypted vault whose key lives in the test's in-memory keystore."""
    vault, _recovery = init_vault(path, keys_from_env())
    return vault
