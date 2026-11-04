"""Vaults made by earlier versions of TaxBinder open: the fixtures
in tests/fixtures/vaults were made by those versions and are never edited. A vault from a newer
version is refused untouched; an older format is upgraded after a backup."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from taxbinder import backup
from taxbinder.entities import list_entities
from taxbinder.models import Document, Institution, Taxpayer
from taxbinder.models.registry import Portal
from taxbinder.store import (
    MemoryKeys,
    RecordStore,
    VaultTooNew,
    fsck,
    init_vault,
    open_vault,
    restore_key,
)
from taxbinder.store import upgrade as upgrade_module
from taxbinder.store import vault as vault_module
from taxbinder.store.kinds import KINDS
from taxbinder.yamlio import dump_yaml, load_yaml

FIXTURES = Path(__file__).parent / "fixtures" / "vaults"
EXAMPLES = Path(__file__).parent / "fixtures" / "examples"
OLD = sorted(FIXTURES.glob("format-*.tbak"))
# Kinds of record added after a fixture was made: it can't have them (make.py makes them now).
ADDED_SINCE: dict[str, set[str]] = {}


def test_a_fixture_for_every_format_still_opened() -> None:
    have = {int(re.search(r"\d+", p.stem).group()) for p in OLD}  # type: ignore[union-attr]
    assert set(range(vault_module.OLDEST, vault_module.FORMAT + 1)) <= have


@pytest.mark.parametrize("fixture", OLD, ids=lambda p: p.stem)
def test_an_older_vault_opens(fixture: Path, tmp_path: Path) -> None:
    key = fixture.with_suffix(".key").read_text().splitlines()[-1]
    backup.restore(fixture, key, tmp_path / "restored")
    household = tmp_path / "restored" / "vault"
    refs = list_entities(household)
    assert [r.slug for r in refs] == ["household", "quill-family-trust"]
    keys, seen = MemoryKeys(), set()
    for ref in refs:
        restore_key(ref.path, keys, key)
        vault = open_vault(ref.path, keys)
        assert fsck(vault) == []
        with RecordStore(vault) as store:
            seen |= {kind for kind in KINDS if store.list(kind)}  # every record loads
            for doc in store.list("document"):
                assert store.blobs.get(doc.sha256)  # decrypts, and matches its checksum
        assert len(vault.history.log()) > 1
    expected = set(KINDS) - ADDED_SINCE.get(fixture.stem, set())
    assert seen == expected, "make.py should give the fixture every kind of record"


@pytest.fixture
def new_vault(tmp_path: Path) -> tuple[Path, MemoryKeys]:
    keys = MemoryKeys()
    vault, _ = init_vault(tmp_path / "vault", keys)
    with RecordStore(vault) as store, store.operation("test", "a taxpayer"):
        store.put(Taxpayer(id="primary", name="Avery Quill"), None)
    return vault.root, keys


def test_a_newer_vault_is_refused_untouched(new_vault: tuple[Path, MemoryKeys]) -> None:
    root, keys = new_vault
    info = root / "records" / "vault.yaml"
    newer = re.sub(r"format: \d+", "format: 99", info.read_text()) + "made_by: a later version\n"
    info.write_text(newer)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(VaultTooNew, match="newer version of TaxBinder"):
        open_vault(root, keys)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_an_older_format_is_backed_up_then_upgraded(
    new_vault: tuple[Path, MemoryKeys], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, keys = new_vault
    ran, n = [], vault_module.FORMAT  # a step to a next format, made up for the test
    monkeypatch.setattr(vault_module, "FORMAT", n + 1)
    monkeypatch.setattr(upgrade_module, "STEPS", {n: lambda store: ran.append(store)})
    with RecordStore(open_vault(root, keys)) as store:
        assert len(ran) == 1
        assert store.get("taxpayer", "primary")[0].name == "Avery Quill"
    assert f"format: {n + 1}" in (root / "records" / "vault.yaml").read_text()
    assert open_vault(root, keys).history.log()[0][1] == f"upgrade vault format {n} to {n + 1}"
    made = list((root.parent / upgrade_module.BACKUP_DIR).glob("*.tbak"))
    assert len(made) == 1
    assert backup.last(root) is None  # not counted as the user's own backup
    with RecordStore(open_vault(root, keys)):
        assert len(ran) == 1  # done once


def test_a_failed_step_keeps_the_old_format(
    new_vault: tuple[Path, MemoryKeys], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, keys = new_vault

    def broken(store: RecordStore) -> None:
        raise ValueError("step went wrong")

    n = vault_module.FORMAT
    monkeypatch.setattr(vault_module, "FORMAT", n + 1)
    monkeypatch.setattr(upgrade_module, "STEPS", {n: broken})
    with pytest.raises(upgrade_module.UpgradeFailed, match=upgrade_module.BACKUP_DIR):
        RecordStore(open_vault(root, keys))
    assert f"format: {n}" in (root / "records" / "vault.yaml").read_text()
    with pytest.raises(upgrade_module.UpgradeFailed):  # the lock was released
        RecordStore(open_vault(root, keys))


def test_format_3_drops_the_download_connectors_fields(new_vault: tuple[Path, MemoryKeys]) -> None:
    """2 → 3: a portal's connector, credential_ref and mfa (the portal itself when it held only
    those), an institution's notifications and a document's acquired.from go; nothing else."""
    root, keys = new_vault
    records = root / "records"
    doc = Document.model_validate(load_yaml((EXAMPLES / "document.doc_ab12cd34ef56.yaml").read_text()))
    with RecordStore(open_vault(root, keys)) as store, store.operation("test", "records"):
        store.put(Institution(id="fernhollow", name="Fernhollow", portal=Portal(url="https://f.example")),
                  None)  # fmt: skip
        store.put(Institution(id="tidemark", name="TideMark"), None)
        store.put(doc, None)
    fern = KINDS["institution"].path("fernhollow")
    tide = KINDS["institution"].path("tidemark")
    docf = KINDS["document"].path(doc.id)

    def edit(rel: str, change: Callable[[dict], object]) -> None:
        data = load_yaml((records / rel).read_text())
        change(data)
        (records / rel).write_text(dump_yaml(data))

    def fern_fields(d: dict) -> None:
        d["portal"].update(connector="generic", credential_ref="op://a/b", mfa=None)  # null counts
        d["notifications"] = {"email_from": ["x@example.com"]}

    def doc_fields(d: dict) -> None:
        d["acquired"]["from"] = "fernhollow"
        d["reacquired"] = [{"at": d["acquired"]["at"], "via": "upload", "from": None}]

    edit(fern, fern_fields)
    edit(tide, lambda d: d.update(portal={"connector": "tidemark"}))
    edit(docf, doc_fields)
    info = records / "vault.yaml"
    info.write_text(info.read_text().replace(f"format: {vault_module.FORMAT}", "format: 2"))
    taxpayer = (records / KINDS["taxpayer"].path("primary")).read_bytes()
    open_vault(root, keys).history.commit([fern, tide, docf, "vault.yaml"], "test", "format 2")

    with RecordStore(open_vault(root, keys)) as store:
        assert store.get("institution", "fernhollow")[0].portal == Portal(url="https://f.example")
        assert store.get("institution", "tidemark")[0].portal is None
        d = store.get("document", doc.id)[0]
        assert d.acquired.via == doc.acquired.via and len(d.reacquired) == 1
    for rel in (fern, tide, docf):
        text = (records / rel).read_text()
        for word in ("connector", "credential_ref", "mfa", "notifications", "from:"):
            assert word not in text, (rel, word)
    assert (records / KINDS["taxpayer"].path("primary")).read_bytes() == taxpayer  # untouched
    assert "format: 3" in info.read_text()
    log = [message for _, message in open_vault(root, keys).history.log()[:2]]
    assert log == ["upgrade vault format 2 to 3", "upgrade: download connectors' fields removed"]
