"""Backups: one encrypted file with every vault; restored with the recovery key (fictional data)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pdfgen import make_pdf
from pyrage import x25519
from test_web import state  # noqa: F401 - fixture

from taxbinder import backup
from taxbinder.entities import create_entity
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Taxpayer
from taxbinder.store import MemoryKeys, RecordStore, init_vault, open_vault


def _vault(tmp_path: Path):  # noqa: ANN202
    keys = MemoryKeys()
    vault, recovery = init_vault(tmp_path / "app" / "vault", keys)
    store = RecordStore(vault)
    with store.operation("t", "fixture"):
        store.put(Taxpayer(id="me", name="Jonathan Example", tin="900-22-4321"), None)
    pdf = vault.inbox / "k1.pdf"
    pdf.write_bytes(make_pdf(["Schedule K-1 (Form 1065) 2025", "Harborview Fund", "Box 2 -12,345"]))
    ingest_file(store, pdf, "upload", "t")
    store.close()
    create_entity(vault.root, keys, "Northwind Holdings Inc.", "1120", "t")
    return keys, vault, recovery


def test_backup_round_trip_with_the_recovery_key(tmp_path: Path) -> None:
    keys, vault, recovery = _vault(tmp_path)
    made = backup.create(
        open_vault(vault.root, keys), tmp_path / "out", datetime(2026, 10, 4, 12, tzinfo=UTC)
    )
    assert made.path.name == "TaxBinder backup 2026-10-04.tbak" and made.vaults == 2
    raw = made.path.read_bytes()
    assert raw.startswith(b"age-encryption.org/v1")  # nothing readable: not even names
    for secret in (b"Jonathan", b"Harborview", b"Northwind", b"12,345", b"records"):
        assert secret not in raw
    assert backup.last(vault.root)["name"] == made.path.name

    again = backup.create(
        open_vault(vault.root, keys), tmp_path / "out", datetime(2026, 10, 4, 12, tzinfo=UTC)
    )
    assert again.path.name == "TaxBinder backup 2026-10-04 (2).tbak"  # never overwritten

    target = tmp_path / "restored"
    manifest = backup.restore(made.path, recovery, target)
    assert not list(target.rglob("index.sqlite"))  # left out; rebuilt when the vault is opened
    assert manifest["vaults"] == ["vault", "entities/northwind-holdings-inc/vault"]
    fresh = MemoryKeys()  # another Mac: open the restored vault with the recovery key
    from taxbinder.store import restore_key

    restored = RecordStore(restore_key(target / "vault", fresh, recovery))
    assert restored.get("taxpayer", "me")[0].tin == "900-22-4321"
    assert len(restored.list("document")) == 1
    doc = restored.list("document")[0]
    assert restored.blobs.get(doc.sha256).startswith(b"%PDF")
    assert (target / "entities" / "northwind-holdings-inc" / "vault" / "records").is_dir()
    restored.close()


def test_restore_refuses_a_wrong_key_and_a_used_folder(tmp_path: Path) -> None:
    keys, vault, recovery = _vault(tmp_path)
    made = backup.create(open_vault(vault.root, keys), tmp_path / "out")
    with pytest.raises(ValueError, match="doesn't open"):
        backup.restore(made.path, str(x25519.Identity.generate()), tmp_path / "x")
    assert not (tmp_path / "x").exists()
    with pytest.raises(ValueError, match="isn't a TaxBinder key"):
        backup.restore(made.path, "not a key", tmp_path / "y")
    used = tmp_path / "used"
    used.mkdir()
    (used / "something").write_text("keep me")
    with pytest.raises(ValueError, match="isn't empty"):
        backup.restore(made.path, recovery, used)
    assert (used / "something").read_text() == "keep me"


def test_backup_button(state, tmp_path: Path) -> None:  # noqa: ANN001, F811
    from test_web import ORIGIN, client

    state.downloads_dir = tmp_path / "downloads"
    c = client(state)
    assert "No backup yet." in c.get("/backup").text
    r = c.post("/api/backup", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["vaults"] == 1, r.text
    assert (state.downloads_dir / r.json()["name"]).read_bytes().startswith(b"age-encryption.org")
    html = c.get("/backup").text
    assert "Last backup" in html and "today" in html


def test_backup_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli, runner

    from taxbinder.cli import app

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    cli("init")
    out = cli("backup", "create", "--out", tmp_path / "out")
    assert "saved" in out and "1 vault(s)" in out
    assert "last backup:" in cli("backup", "status")
    made = next((tmp_path / "out").glob("*.tbak"))
    bad = runner.invoke(
        app, ["backup", "restore", str(made), "--to", str(tmp_path / "r")], input="not-a-key\n"
    )
    assert bad.exit_code != 0 and "isn't a TaxBinder key" in bad.output


def test_this_macs_key_works_like_the_recovery_key(tmp_path: Path) -> None:
    """For a lost recovery key: the key `taxbinder key show` prints opens vault and backups."""
    from taxbinder.store import restore_key, vault_key

    keys, vault, _recovery = _vault(tmp_path)
    secret = vault_key(vault.root, keys)
    assert secret.startswith("AGE-SECRET-KEY-")
    made = backup.create(open_vault(vault.root, keys), tmp_path / "out")
    backup.restore(made.path, secret, tmp_path / "r")  # opens the backup
    store = RecordStore(restore_key(tmp_path / "r" / "vault", MemoryKeys(), secret))  # a new Mac
    assert store.get("taxpayer", "me")[0].tin == "900-22-4321"
    store.close()


def test_key_show_asks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli, runner

    from taxbinder.cli import app

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    cli("init")
    no = runner.invoke(app, ["key", "show"], input="n\n")
    assert no.exit_code != 0 and "AGE-SECRET-KEY-" not in no.output
    yes = runner.invoke(app, ["key", "show"], input="y\n")
    assert yes.exit_code == 0 and "AGE-SECRET-KEY-" in yes.output
