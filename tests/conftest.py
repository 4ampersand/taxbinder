"""Every test uses an in-memory keystore; touching the macOS Keychain is a test failure."""

from __future__ import annotations

import pytest

from taxbinder.store import keys as keys_module


@pytest.fixture(autouse=True)
def no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise AssertionError("tests must never touch the macOS Keychain")

    monkeypatch.setenv("TAXBINDER_KEYSTORE", "memory")
    monkeypatch.setattr(keys_module, "_MEMORY", keys_module.MemoryKeys())
    monkeypatch.setattr(keys_module.KeychainKeys, "get", refuse)
    monkeypatch.setattr(keys_module.KeychainKeys, "set", refuse)
    import keyring  # and keyring itself, so nothing reaches the Keychain

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, refuse)
