"""Vault consistency check (§4.5, §12.2): records, blob references, blob integrity."""

from __future__ import annotations

from taxbinder.models import Document
from taxbinder.store.atomic import TMP_SUFFIX
from taxbinder.store.blobs import BlobStore
from taxbinder.store.codec import FieldCodec, PlainFieldCodec
from taxbinder.store.kinds import KINDS, record_id
from taxbinder.store.vault import Vault
from taxbinder.yamlio import load_yaml


def fsck(vault: Vault) -> list[str]:
    """Return a list of problems; empty means the vault is consistent. Read-only."""
    codec: FieldCodec = vault.crypto.fields if vault.crypto else PlainFieldCodec()
    blobs = BlobStore(vault.blobs, vault.crypto.blobs if vault.crypto else None)
    problems: list[str] = []
    seen = {vault.info_file.resolve()}

    for k in KINDS.values():
        for path in sorted(vault.records.glob(k.glob(None))):
            rel = path.relative_to(vault.records)
            seen.add(path.resolve())
            try:
                record = k.adapter.validate_python(codec.decode(load_yaml(path.read_text())))
            except Exception as e:  # noqa: BLE001 - report every kind of failure
                problems.append(f"invalid record {rel}: {e}")
                continue
            expected = k.path(record_id(record))
            if str(rel) != expected:
                problems.append(f"misplaced record {rel}: its id belongs at {expected}")
            if isinstance(record, Document):
                blob = vault.blobs / record.blob
                if not blob.exists():
                    problems.append(f"missing blob for {record.id}: {record.blob}")
                elif not blob.name.startswith(record.sha256):
                    problems.append(f"blob name for {record.id} doesn't match its sha256")

    for path in sorted(vault.records.rglob("*.yaml")):
        if path.resolve() not in seen:
            problems.append(f"unknown file {path.relative_to(vault.records)}")

    problems.extend(blobs.verify_all())

    for root in (vault.records, vault.blobs):
        for path in sorted(root.rglob(f"*{TMP_SUFFIX}")):
            problems.append(f"leftover temp file {path.relative_to(vault.root)}")
    return problems
