"""Vault hardening (§4.5, §4.6): encryption, recovery key, history."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from pdfgen import make_pdf
from pyrage import x25519
from quills import build

from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import CLAIM_ADAPTER, Institution, Taxpayer
from taxbinder.store import (
    MemoryKeys,
    RecordNotFound,
    RecordStore,
    VaultKeyMissing,
    WrongKey,
    fsck,
    init_vault,
    open_vault,
    restore_key,
)
from taxbinder.yamlio import dump_yaml

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
TIN = "900-45-6789"
CONFIRMATION = "EFTPS-778899001122"
PRINTED_ACCOUNT = "Account Number: 9900-31-7731"


@pytest.fixture
def seeded(tmp_path: Path):
    """An encrypted vault with the test household, a TIN, a confirmation number and a PDF."""
    keys = MemoryKeys()
    vault, recovery = init_vault(tmp_path / "vault", keys)
    with RecordStore(vault) as store:
        build(store, "avery")
        taxpayer, rev = store.get("taxpayer", "primary")
        payment = CLAIM_ADAPTER.validate_python(
            {
                "id": "2025/payments/fed.2026-04-10",
                "year": 2025,
                "category": "tax_payment",
                "jurisdiction": "federal",
                "date": "2026-04-10",
                "amount": 1000,
                "payment": {
                    "kind": "balance_due",
                    "applied_to_year": 2025,
                    "confirmation_number": CONFIRMATION,
                },
            }
        )
        with store.operation("avery", "add tin and payment"):
            store.put(Taxpayer.model_validate({**taxpayer.model_dump(), "tin": TIN}), rev)
            store.put(payment, None)
        pdf = vault.inbox / "ms.pdf"
        pdf.write_bytes(make_pdf(["Brackley Wealth 2025 Form 1099-B", PRINTED_ACCOUNT]))
        ingest_file(store, pdf, "upload", "avery", now=NOW)
        secrets = [TIN, CONFIRMATION, "9900-31-7731"] + [
            i.number for s in store.list("source") for i in s.identifiers
        ]
    return vault, keys, recovery, secrets


def _all_bytes(vault_root: Path) -> list[tuple[str, bytes]]:
    """Every file under the vault, plus every git object decompressed."""
    out = [(str(p), p.read_bytes()) for p in vault_root.rglob("*") if p.is_file()]
    with Repo(str(vault_root / "records")) as repo:
        for sha in repo.object_store:
            out.append((f"git:{sha.decode()}", repo.object_store[sha].as_raw_string()))
    return out


def test_no_plaintext_identifier_anywhere(seeded) -> None:
    vault, _, _, secrets = seeded
    assert len(secrets) >= 8
    leaks = [
        where
        for where, data in _all_bytes(vault.root)
        for secret in secrets
        if secret.encode() in data or secret.replace(" ", "").encode() in data
    ]
    assert leaks == []
    source = (vault.records / "registry/sources/tidemark.tidemark-premiumsavings.yaml").read_text()
    assert "ENC[age:" in source and "…0006" in source  # hint stays readable


def test_blobs_are_encrypted_at_rest(seeded) -> None:
    vault, *_ = seeded
    [blob] = [p for p in vault.blobs.rglob("*") if p.is_file()]
    assert blob.name.endswith(".pdf.age") and b"%PDF" not in blob.read_bytes()
    assert b"Brackley Wealth" not in blob.read_bytes()
    assert fsck(vault) == []


def test_recovery_key_alone_reads_everything(seeded) -> None:
    vault, _, recovery, _ = seeded
    fresh = MemoryKeys()  # a new machine: nothing in its Keychain
    with pytest.raises(VaultKeyMissing):
        open_vault(vault.root, fresh)
    with pytest.raises(WrongKey):
        restore_key(vault.root, fresh, str(x25519.Identity.generate()))
    restored = restore_key(vault.root, fresh, recovery)
    with RecordStore(restored) as store:
        taxpayer, _ = store.get("taxpayer", "primary")
        assert taxpayer.tin == TIN
        [doc] = store.list("document")
        assert store.blobs.get(doc.sha256).startswith(b"%PDF")
    assert open_vault(vault.root, fresh) == vault


def test_unchanged_values_keep_their_ciphertext(seeded) -> None:
    vault, keys, *_ = seeded
    path = vault.records / "registry/taxpayers/primary.yaml"
    before = path.read_text()
    with RecordStore(open_vault(vault.root, keys)) as store:
        taxpayer, rev = store.get("taxpayer", "primary")
        with store.operation("avery", "no-op"):
            assert store.put(taxpayer, rev) == rev
        with store.operation("avery", "rename"):
            store.put(taxpayer.model_copy(update={"name": "Renamed"}), rev)
    after = path.read_text()
    tin_line = next(line for line in before.splitlines() if line.startswith("tin:"))
    assert tin_line in after and "Renamed" in after


def test_one_commit_per_operation(seeded) -> None:
    vault, keys, *_ = seeded
    log = vault.history.log()
    assert [message for _, message in reversed(log)] == [
        "create vault",
        "test household",
        "add tin and payment",
        "ingest: 1099_composite 2025 brackley-wealth.consolidated-tax-statement-7731 (doc_"
        + log[0][1].split("(doc_")[1],
    ]
    assert log[1][0].startswith("avery <")
    with RecordStore(open_vault(vault.root, keys)) as store, store.operation("avery", "noop"):
        pass
    assert len(vault.history.log()) == len(log)  # empty operations don't commit
    staged, unstaged, untracked = porcelain.status(str(vault.records))
    assert (staged, unstaged, untracked) == ({"add": [], "delete": [], "modify": []}, [], [])


def test_wrong_or_missing_key(seeded) -> None:
    vault, keys, *_ = seeded
    other = MemoryKeys()
    info_id = next(
        line.split(": ")[1]
        for line in vault.info_file.read_text().splitlines()
        if line.startswith("id:")
    )
    other.set(info_id, str(x25519.Identity.generate()))
    with pytest.raises(WrongKey):
        open_vault(vault.root, other)


def test_unencrypted_dev_vault_rejected(tmp_path: Path) -> None:
    records = tmp_path / "old" / "records"
    records.mkdir(parents=True)
    (records / "vault.yaml").write_text(
        dump_yaml({"id": "x", "format": 1, "encrypted": False, "created": NOW})
    )
    with pytest.raises(RuntimeError, match="early test version"):
        open_vault(tmp_path / "old", MemoryKeys())


def test_institution_without_secrets_is_plain(seeded) -> None:
    vault, keys, *_ = seeded
    with RecordStore(open_vault(vault.root, keys)) as store, store.operation("t", "add"):
        store.put(Institution(id="new-fund", name="New Fund"), None)
    assert "ENC[" not in (vault.records / "registry/institutions/new-fund.yaml").read_text()


def test_delete_is_recorded_in_history(seeded) -> None:
    vault, keys, *_ = seeded
    exp_id = "2025/fernhollow-properties.kettle-410.k1"
    with RecordStore(open_vault(vault.root, keys)) as store:
        _, rev = store.get("expectation", exp_id)
        with store.operation("avery", "delete row"):
            store.delete("expectation", exp_id, rev)
        with pytest.raises(RecordNotFound):
            store.get("expectation", exp_id)
        assert exp_id not in {e.id for e in store.list("expectation", 2025)}
        with pytest.raises(RecordNotFound), store.operation("avery", "again"):
            store.delete("expectation", exp_id, rev)
    assert vault.history.log()[0] == (vault.history.log()[0][0], "delete row")
    staged, unstaged, untracked = porcelain.status(str(vault.records))
    assert not any(staged.values()) and not unstaged and not untracked  # clean tree
    assert fsck(vault) == []


def test_live_changes_rule() -> None:
    from types import SimpleNamespace as C

    from taxbinder.store.records import live_changes

    def ch(sha: str, message: str, author: str = "app:avery") -> C:
        return C(
            sha=sha * 40,
            message=message,
            automatic=message.startswith("reconcile") or author.startswith("auto:"),
        )

    a, b = "a", "b"
    later = [
        ch("u", f"undo: edit b [{b * 12}]"),  # undoes b, which is also later → both cancel
        ch("r", "reconcile 2025"),  # automatic
        ch(b, "edit b"),
        ch("v", f"undo: edit z [{'z' * 12}]"),  # undoes something older: still a real change
        ch("e", "edit e"),
    ]
    assert [c.message for c in live_changes(later)] == [f"undo: edit z [{'z' * 12}]", "edit e"]
    assert live_changes([ch(a, "edit a")])[0].message == "edit a"
