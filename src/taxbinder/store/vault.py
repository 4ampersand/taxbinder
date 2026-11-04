"""Vault folder: layout, keys, creation and opening (§4.2, §4.5, §4.6)."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pyrage import x25519

from taxbinder.models.common import Record
from taxbinder.store.crypto import Crypto, WrongKey
from taxbinder.store.history import History
from taxbinder.store.keys import KeyStore
from taxbinder.yamlio import dump_yaml, load_yaml

IDENTITY_FOLDER = "com.4ampersand.taxbinder"  # the identity names its folder (tauri.conf.json)
REAL_VAULT = Path.home() / "Library" / "Application Support" / IDENTITY_FOLDER / "vault"
FORMAT = 3  # raised by a step in store/upgrade.py, never on its own
OLDEST = 2  # the oldest format this version opens (and upgrades); 1 was unencrypted
CHECK = "taxbinder vault key check"


class VaultKeyMissing(Exception):
    """This machine has no key for the vault: restore it with the recovery key."""


class VaultTooNew(RuntimeError):
    """Made by a newer TaxBinder: refused before anything is written."""


class VaultInfo(Record):
    id: str
    format: int
    encrypted: bool
    recipients: list[str] = []  # age public keys: the vault key and the recovery key
    check: str | None = None  # CHECK sealed to the recipients; proves a key opens this vault
    created: datetime


@dataclass(frozen=True)
class Vault:
    root: Path
    crypto: Crypto | None = field(default=None, compare=False, repr=False)

    @property
    def records(self) -> Path:
        return self.root / "records"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def private(self) -> Path:
        return self.root / "private"

    @property
    def inbox(self) -> Path:
        """Files waiting to be ingested; `inbox/portal/` is where portal windows save."""
        return self.root / "inbox"

    @property
    def index(self) -> Path:
        return self.root / "index.sqlite"

    @property
    def lock(self) -> Path:
        return self.root / ".lock"

    @property
    def info_file(self) -> Path:
        return self.records / "vault.yaml"

    @property
    def history(self) -> History:
        return History(self.records)


def default_vault_path() -> Path:
    """`TAXBINDER_VAULT` if set, else the real vault location."""
    env = os.environ.get("TAXBINDER_VAULT")
    return Path(env).expanduser() if env else REAL_VAULT


def init_vault(path: Path, keys: KeyStore) -> tuple[Vault, str]:
    """Create an empty vault. Returns it and the recovery key, which is shown only once."""
    root = path.expanduser()
    vault = Vault(root)
    if vault.info_file.exists():
        raise FileExistsError(f"vault already exists: {vault.root}")
    for d in (root, vault.records, vault.blobs, vault.private, vault.inbox, vault.inbox / "portal"):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)  # the desktop shell may have created inbox/portal already

    key, recovery = x25519.Identity.generate(), x25519.Identity.generate()
    recipients = [str(key.to_public()), str(recovery.to_public())]
    crypto = Crypto.load(str(key), recipients)
    info = VaultInfo(
        id=uuid.uuid4().hex,
        format=FORMAT,
        encrypted=True,
        recipients=recipients,
        check=crypto.seal_text(CHECK),
        created=datetime.now(UTC),
    )
    keys.set(info.id, str(key))
    vault.info_file.write_text(dump_yaml(info.model_dump()))
    History.create(vault.records).commit(["vault.yaml"], "taxbinder", "create vault")
    return Vault(root, crypto), str(recovery)


def init_linked_vault(path: Path, keys: KeyStore, parent: Path) -> Vault:
    """Create an empty vault locked with the same keys as `parent` (an entity beside the
    household): the same Keychain secret and the same printed recovery key open both."""
    parent_info = _info(Vault(parent.expanduser()))
    secret = keys.get(parent_info.id)
    if secret is None:
        raise VaultKeyMissing(f"no key for vault {parent_info.id} on this machine")
    crypto = _unlock(parent_info, secret)
    root = path.expanduser()
    vault = Vault(root)
    if vault.info_file.exists():
        raise FileExistsError(f"vault already exists: {vault.root}")
    for d in (root, vault.records, vault.blobs, vault.private, vault.inbox, vault.inbox / "portal"):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    info = VaultInfo(
        id=uuid.uuid4().hex,
        format=FORMAT,
        encrypted=True,
        recipients=list(parent_info.recipients),
        check=crypto.seal_text(CHECK),
        created=datetime.now(UTC),
    )
    keys.set(info.id, secret)
    vault.info_file.write_text(dump_yaml(info.model_dump()))
    History.create(vault.records).commit(["vault.yaml"], "taxbinder", "create vault")
    return Vault(root, crypto)


def _info(vault: Vault) -> VaultInfo:
    if not vault.info_file.exists():
        raise FileNotFoundError(f"no vault at {vault.root} (missing records/vault.yaml)")
    data = load_yaml(vault.info_file.read_text())
    if isinstance(data, dict) and isinstance(data.get("format"), int) and data["format"] > FORMAT:
        raise VaultTooNew(  # checked first: a newer vault.yaml may have fields this one lacks
            "This vault was made by a newer version of TaxBinder. Update TaxBinder to open it; "
            "nothing was changed."
        )
    info = VaultInfo.model_validate(data)
    if info.format < OLDEST or not info.encrypted:
        raise RuntimeError(  # format 1: an unencrypted development vault
            "This binder was made by an early test version of TaxBinder and can't be opened. "
            "Nothing was changed."
        )
    return info


def _unlock(info: VaultInfo, secret: str) -> Crypto:
    crypto = Crypto.load(secret, info.recipients)
    if info.check is None or crypto.open_text(info.check) != CHECK:
        raise WrongKey("this key doesn't belong to the vault")
    return crypto


def open_vault(path: Path, keys: KeyStore) -> Vault:
    vault = Vault(path.expanduser())
    info = _info(vault)
    secret = keys.get(info.id)
    if secret is None:
        raise VaultKeyMissing(f"no key for vault {info.id} on this machine")
    return Vault(vault.root, _unlock(info, secret))


def vault_key(path: Path, keys: KeyStore) -> str:
    """This machine's key for the vault (checked to open it). Like the recovery key, it opens
    the vault, its entities and its backups anywhere; for when the recovery key is lost."""
    info = _info(Vault(path.expanduser()))
    secret = keys.get(info.id)
    if secret is None:
        raise VaultKeyMissing(f"no key for vault {info.id} on this machine")
    _unlock(info, secret)  # WrongKey if it doesn't open this vault
    return secret


def restore_key(path: Path, keys: KeyStore, recovery: str) -> Vault:
    """Adopt the printed recovery key as this machine's key for the vault."""
    vault = Vault(path.expanduser())
    info = _info(vault)
    try:
        crypto = _unlock(info, recovery.strip())
    except Exception as e:  # noqa: BLE001 - malformed key or wrong key
        raise WrongKey("that recovery key doesn't open this vault") from e
    keys.set(info.id, recovery.strip())
    return Vault(vault.root, crypto)
