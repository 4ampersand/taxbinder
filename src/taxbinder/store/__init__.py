"""Vault storage (§4): RecordStore, encrypted blobs and fields, git history, index, fsck."""

from taxbinder.store.blobs import BlobCorrupt, BlobStore, StoredBlob
from taxbinder.store.crypto import WrongKey
from taxbinder.store.fsck import fsck
from taxbinder.store.keys import FileKeys, KeychainKeys, KeyStore, MemoryKeys, keys_from_env
from taxbinder.store.records import (
    NoOperation,
    RecordNotFound,
    RecordStore,
    RevConflict,
    VaultLocked,
)
from taxbinder.store.vault import (
    Vault,
    VaultKeyMissing,
    VaultTooNew,
    default_vault_path,
    init_linked_vault,
    init_vault,
    open_vault,
    restore_key,
    vault_key,
)

__all__ = [
    "BlobCorrupt",
    "BlobStore",
    "FileKeys",
    "KeyStore",
    "KeychainKeys",
    "MemoryKeys",
    "NoOperation",
    "RecordNotFound",
    "RecordStore",
    "RevConflict",
    "StoredBlob",
    "Vault",
    "VaultKeyMissing",
    "VaultLocked",
    "VaultTooNew",
    "WrongKey",
    "default_vault_path",
    "fsck",
    "init_linked_vault",
    "init_vault",
    "keys_from_env",
    "open_vault",
    "restore_key",
    "vault_key",
]
