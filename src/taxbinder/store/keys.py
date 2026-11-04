"""Where a vault's age identity (secret key) lives (§4.6, §12.2).

- `KeychainKeys`: the macOS Keychain via `keyring` (service `com.4ampersand.taxbinder`); the
  default.
- `MemoryKeys`: this process only (tests).
- `FileKeys`: a JSON file (tests and throwaway dev vaults only; the key sits on disk).

`keys_from_env()` picks one from `TAXBINDER_KEYSTORE`: unset → Keychain, `memory`, `file:<path>`.
While the demo is open (`use()`, demo.py) it gives the demo's own key file instead, so the demo
never touches the Keychain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

SERVICE = "com.4ampersand.taxbinder"


class KeyStore(Protocol):
    def get(self, vault_id: str) -> str | None: ...
    def set(self, vault_id: str, secret: str) -> None: ...
    def delete(self, vault_id: str) -> None: ...  # gone for good; nothing to delete is fine


class KeychainKeys:
    def get(self, vault_id: str) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, f"vault:{vault_id}")

    def set(self, vault_id: str, secret: str) -> None:
        import keyring

        keyring.set_password(SERVICE, f"vault:{vault_id}", secret)

    def delete(self, vault_id: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, f"vault:{vault_id}")
        except PasswordDeleteError:
            pass  # not there


class MemoryKeys:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def get(self, vault_id: str) -> str | None:
        return self._keys.get(vault_id)

    def set(self, vault_id: str, secret: str) -> None:
        self._keys[vault_id] = secret

    def delete(self, vault_id: str) -> None:
        self._keys.pop(vault_id, None)


class FileKeys:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _all(self) -> dict[str, str]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, vault_id: str) -> str | None:
        return self._all().get(vault_id)

    def set(self, vault_id: str, secret: str) -> None:
        self._write(self._all() | {vault_id: secret})

    def delete(self, vault_id: str) -> None:
        data = self._all()
        if data.pop(vault_id, None) is not None:
            self._write(data)

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600)
        self.path.write_text(json.dumps(data))


_MEMORY = MemoryKeys()
_IN_USE: KeyStore | None = None  # the demo's keys, while it's open


def use(keys: KeyStore | None) -> None:
    """Every vault opened from now on uses `keys` (the demo's), until `use(None)`."""
    global _IN_USE
    _IN_USE = keys


def keys_from_env() -> KeyStore:
    if _IN_USE is not None:
        return _IN_USE
    choice = os.environ.get("TAXBINDER_KEYSTORE", "")
    if choice == "memory":
        return _MEMORY
    if choice.startswith("file:"):
        return FileKeys(Path(choice[5:]).expanduser())
    if choice:
        raise ValueError(f"unknown TAXBINDER_KEYSTORE {choice!r} (use memory or file:<path>)")
    return KeychainKeys()
