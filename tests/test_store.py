"""Vault and RecordStore (§4.1 write discipline). Temp vaults only."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from test_models_roundtrip import EXAMPLES, FILES, SHAPES

from taxbinder.models import Document, Institution
from taxbinder.store import (
    BlobCorrupt,
    BlobStore,
    NoOperation,
    RecordNotFound,
    RecordStore,
    RevConflict,
    VaultLocked,
    atomic,
    fsck,
    init_vault,
    keys_from_env,
    open_vault,
)
from taxbinder.store.kinds import kind_of, record_id
from taxbinder.store.vault import REAL_VAULT, Vault, default_vault_path
from taxbinder.yamlio import from_yaml

CASH_DONATION = EXAMPLES / "claim.donation-cash.yaml"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return new_vault(tmp_path / "vault")


@pytest.fixture
def store(vault: Vault):
    with RecordStore(vault) as s:
        yield s


def inst(name: str = "Fernhollow properties") -> Institution:
    return Institution(id="fernhollow-properties", name=name)


def put(store: RecordStore, record, rev: str | None = None) -> str:
    with store.operation("tester", "test write"):
        return store.put(record, rev)


# -- vault ---------------------------------------------------------------------------------


def test_init_creates_layout(vault: Vault) -> None:
    for d in (vault.records, vault.blobs, vault.private, vault.inbox, vault.inbox / "portal"):
        assert d.is_dir()
        assert d.stat().st_mode & 0o777 == 0o700
    assert open_vault(vault.root, keys_from_env()) == vault
    with pytest.raises(FileExistsError):
        init_vault(vault.root, keys_from_env())


def test_default_vault_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAXBINDER_VAULT", raising=False)
    assert default_vault_path() == REAL_VAULT
    monkeypatch.setenv("TAXBINDER_VAULT", "~/.taxbinder-dev/vault")
    assert default_vault_path() == Path.home() / ".taxbinder-dev" / "vault"


# -- single writer and optimistic concurrency ----------------------------------------------


def test_second_writer_rejected(vault: Vault) -> None:
    with RecordStore(vault):
        with pytest.raises(VaultLocked):
            RecordStore(vault)
    RecordStore(vault).close()  # released on close


def test_stale_rev_rejected_and_file_unchanged(store: RecordStore) -> None:
    rev1 = put(store, inst())
    rev2 = put(store, inst("Fernhollow properties LLC"), rev1)
    path = store.vault.records / "registry/institutions/fernhollow-properties.yaml"
    before = path.read_bytes()

    with pytest.raises(RevConflict) as err:
        put(store, inst("Stale edit"), rev1)
    assert err.value.current_rev == rev2
    assert path.read_bytes() == before


def test_create_over_existing_rejected(store: RecordStore) -> None:
    put(store, inst())
    with pytest.raises(RevConflict):
        put(store, inst("Duplicate"))


def test_write_requires_operation(store: RecordStore) -> None:
    with pytest.raises(NoOperation):
        store.put(inst(), None)


def test_operation_groups_writes(store: RecordStore) -> None:
    with store.operation("avery", "add two institutions"):
        store.put(inst(), None)
        store.put(Institution(id="tidemark", name="TideMark"), None)
    op = store.completed[-1]
    assert (op.actor, op.message) == ("avery", "add two institutions")
    assert op.paths == [
        "registry/institutions/fernhollow-properties.yaml",
        "registry/institutions/tidemark.yaml",
    ]


def test_unchanged_put_is_a_no_op(store: RecordStore) -> None:
    rev = put(store, inst())
    assert put(store, inst(), rev) == rev
    assert store.completed[-1].paths == ["registry/institutions/fernhollow-properties.yaml"]
    assert len(store.completed) == 1  # the no-op didn't produce an operation


# -- crash safety --------------------------------------------------------------------------


def test_failure_mid_write_leaves_old_file(
    store: RecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    rev = put(store, inst())
    path = store.vault.records / "registry/institutions/fernhollow-properties.yaml"
    before = path.read_bytes()

    def crash(*_: object) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(atomic.os, "replace", crash)
    with pytest.raises(OSError, match="simulated"):
        put(store, inst("Half written"), rev)

    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []
    monkeypatch.undo()
    assert store.get("institution", "fernhollow-properties") == (inst(), rev)


# -- records -------------------------------------------------------------------------------


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_put_get_every_record_type(store: RecordStore, path: Path) -> None:
    shape = SHAPES[path.name.split(".")[0]]
    record = from_yaml(path.read_text(), shape)
    if shape.__name__ == "Finding":
        pytest.skip("single findings are stored inside a FindingSet (years/<year>/findings.yaml)")
    rev = put(store, record)
    kind = kind_of(record)

    assert store.get(kind.name, record_id(record)) == (record, rev)
    assert store.list(kind.name) == [record]
    assert (store.vault.records / kind.path(record_id(record))).exists()


def test_paths_follow_layout(store: RecordStore) -> None:
    claim = from_yaml(CASH_DONATION.read_text(), SHAPES["claim"])
    put(store, claim)
    assert (
        store.vault.records
        / "years/2025/claims/donations/2025-02-26.friends-of-the-marsh-wrens.yaml"
    ).exists()


def test_list_by_year(store: RecordStore) -> None:
    claim = from_yaml(CASH_DONATION.read_text(), SHAPES["claim"])
    put(store, claim)
    assert store.list("claim", 2025) == [claim]
    assert store.list("claim", 2024) == []


def test_missing_and_invalid_ids(store: RecordStore) -> None:
    with pytest.raises(RecordNotFound):
        store.get("institution", "nope")
    for bad in ["../../outside", "a/b", "UPPER"]:
        with pytest.raises(ValueError, match="invalid"):
            store.get("institution", bad)
    with pytest.raises(ValueError, match="invalid"):
        store.get("claim", "2025/../../x")


def test_reindex_rebuilds_same_rows(store: RecordStore) -> None:
    # claim.resolved-assertions reuses the DAF claim's id.
    seed = [p for p in FILES if not p.name.startswith(("finding.", "claim.resolved"))]
    with store.operation("tester", "seed"):
        for path in seed:
            store.put(from_yaml(path.read_text(), SHAPES[path.name.split(".")[0]]), None)
    rows = store.index_rows()
    assert rows

    store.vault.index.unlink()
    store.close()
    with RecordStore(store.vault) as fresh:
        assert fresh.index_rows() == []
        assert fresh.reindex() == len(rows)
        assert fresh.index_rows() == rows


# -- blobs and fsck ------------------------------------------------------------------------


def add_document(store: RecordStore, blobs: BlobStore, data: bytes) -> Document:
    blob = blobs.put(data, "pdf")
    doc = Document(
        id=f"doc_{blob.sha256[:12]}",
        sha256=blob.sha256,
        blob=blob.path,
        original_filename="synthetic.pdf",
        mime="application/pdf",
        bytes=len(data),
        acquired={"at": datetime(2026, 3, 14, tzinfo=UTC), "via": "upload"},
    )
    put(store, doc)
    return doc


def test_blob_dedupe_and_round_trip(vault: Vault) -> None:
    blobs = RecordStore(vault).blobs
    data = b"%PDF-1.4 synthetic"
    first, second = blobs.put(data, "pdf"), blobs.put(data, "pdf")
    assert first == second
    sha = hashlib.sha256(data).hexdigest()
    assert first.path == f"{sha[:2]}/{sha[2:4]}/{sha}.pdf.age"
    assert blobs.get(sha) == data
    assert len(list(vault.blobs.rglob("*.pdf.age"))) == 1
    assert b"%PDF" not in (vault.blobs / first.path).read_bytes()  # encrypted at rest


def test_fsck_clean_vault(store: RecordStore) -> None:
    add_document(store, store.blobs, b"%PDF-1.4 synthetic")
    put(store, inst())
    assert fsck(store.vault) == []


def test_fsck_detects_tampered_blob(store: RecordStore) -> None:
    blobs = store.blobs
    doc = add_document(store, blobs, b"%PDF-1.4 original")
    path = store.vault.blobs / doc.blob
    path.chmod(0o600)
    path.write_bytes(b"%PDF-1.4 tampered")

    problems = fsck(store.vault)
    assert len(problems) == 1 and doc.blob in problems[0] and "decrypted" in problems[0]
    with pytest.raises(BlobCorrupt):
        blobs.get(doc.sha256)


def test_fsck_detects_missing_blob_misplaced_record_and_temp_files(store: RecordStore) -> None:
    blobs = store.blobs
    doc = add_document(store, blobs, b"%PDF-1.4 x")
    (store.vault.blobs / doc.blob).unlink()
    put(store, inst())
    records = store.vault.records
    (records / "registry/institutions/fernhollow-properties.yaml").rename(
        records / "registry/institutions/wrong-name.yaml"
    )
    (records / "registry/institutions/.x.yaml.abc.tmp").write_text("partial")

    problems = "\n".join(fsck(store.vault))
    assert "missing blob" in problems
    assert "misplaced record registry/institutions/wrong-name.yaml" in problems
    assert "leftover temp file" in problems


# -- all or nothing: a change that doesn't finish leaves nothing half-done -------


def test_a_change_that_fails_half_way_is_put_back(store: RecordStore) -> None:
    put(store, inst())
    with pytest.raises(RuntimeError), store.operation("tester", "half a change"):
        store.put(
            inst("Fernhollow Properties LLC"), store.get("institution", "fernhollow-properties")[1]
        )
        store.put(Institution(id="tidemark", name="TideMark"), None)
        raise RuntimeError("stopped")
    assert store.get("institution", "fernhollow-properties")[0].name == "Fernhollow properties"
    assert [i.id for i in store.list("institution")] == ["fernhollow-properties"]
    assert not store.vault.history.unsaved()
    put(store, Institution(id="lantern-bay", name="Lantern Bay"))  # the next change is its own
    last = store.vault.history.changes(limit=1)[0]
    assert [p.rsplit("/", 1)[-1] for p in last.paths] == ["lantern-bay.yaml"]


def test_a_change_cut_off_by_a_quit_is_put_back_on_open(vault: Vault) -> None:
    with RecordStore(vault) as s:
        put(s, inst())
        rev = s.get("institution", "fernhollow-properties")[1]
        s._op = __import__("taxbinder.store.records", fromlist=["Operation"]).Operation("t", "x")
        s.put(inst("Changed"), rev)  # written, never committed: the app stopped here
        s.put(Institution(id="tidemark", name="TideMark"), None)
        s._op = None
    with RecordStore(vault) as s:
        assert s.get("institution", "fernhollow-properties")[0].name == "Fernhollow properties"
        assert [i.id for i in s.list("institution")] == ["fernhollow-properties"]
        assert not s.vault.history.unsaved()


def test_records_are_read_once_until_their_file_changes(store: RecordStore) -> None:
    """A record file is
    parsed once; a change on disk, by this store or anything else, is read again; every caller
    gets a copy of its own."""
    from taxbinder.store.atomic import write_atomic

    rev = put(store, inst())
    path = store.vault.records / "registry/institutions/fernhollow-properties.yaml"
    a, b = store.list("institution")[0], store.get("institution", "fernhollow-properties")[0]
    assert a is not b and a == b  # copies of one reading
    a.name = "changed by a caller"
    assert store.get("institution", "fernhollow-properties")[0].name == "Fernhollow properties"
    rev2 = put(store, inst("Fernhollow properties LLC"), rev)
    assert store.get("institution", "fernhollow-properties") == (inst("Fernhollow properties LLC"), rev2)
    raw = path.read_bytes().replace(b"Fernhollow properties LLC", b"Fernhollow Holdings")
    write_atomic(path, raw)  # another program (the CLI) changes the file
    assert store.list("institution")[0].name == "Fernhollow Holdings"
    with store.operation("tester", "remove"):
        store.delete("institution", "fernhollow-properties", hashlib.sha256(raw).hexdigest())
    assert store.list("institution") == []
    with pytest.raises(RecordNotFound):
        store.get("institution", "fernhollow-properties")
