"""CLI end to end on a temp vault: init → (test household) → ingest → reconcile → status."""

from __future__ import annotations

from pathlib import Path

import pytest
from pdfgen import make_pdf
from quills import ROWS, build_at
from typer.testing import CliRunner

from taxbinder.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vault"
    monkeypatch.setenv("TAXBINDER_VAULT", str(path))
    return path


def cli(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    if ok:
        assert result.exit_code == 0, result.output
    else:
        assert result.exit_code != 0, result.output
    return result.output


def seed(tmp_path: Path) -> Path:
    cli("init")
    build_at(tmp_path / "vault")
    k1 = tmp_path / "Kettle410_2025_K1.pdf"
    k1.write_bytes(
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    )
    return k1


def test_end_to_end(tmp_path: Path) -> None:
    k1 = seed(tmp_path)
    out = cli("ingest", k1)
    assert "linked to 2025/fernhollow-properties.kettle-410.k1" in out
    assert k1.exists()  # the user's file is copied, not moved

    out = cli("reconcile", "--year", "2025")
    assert "errors" in out and "claims updated" in out

    out = cli("status", "--year", "2025", "--all", "--outstanding")
    missing = out.split("What is missing?")[1].split("What conflicts?")[0]
    conflicts = out.split("What conflicts?")[1].split("What needs my attention?")[0]
    assert "Friends of the Marsh Wrens $2,000.00" in missing
    assert "stock gift: the broker's transfer record" in missing
    assert "FinCEN 114" in missing
    assert "federal payments total $36,000.00" in conflicts
    assert "GiftWell: amounts disagree" in conflicts
    assert "not verified" not in missing  # reviews are "attention", not "missing"
    assert "Outstanding (" in out
    assert "2025/copperfield-collective.pikeway-inv-partners.k1 (expected)" in out

    assert "vault OK" in cli("fsck")
    assert "indexed" in cli("reindex")


def test_claims_links_decisions(tmp_path: Path) -> None:
    seed(tmp_path)
    out = cli(
        "claim", "donation", "--payee", "Food Bank", "--amount", "300", "--date", "2025-12-01"
    )
    claim_id = out.split("added ")[1].strip()
    assert claim_id == "2025/donations/2025-12-01.food-bank"
    out = cli(
        "claim",
        "payment",
        "--jurisdiction",
        "federal",
        "--amount",
        "1000",
        "--date",
        "2026-01-10",
        "--kind",
        "estimated_q4",
    )
    assert "2025/payments/fed.2026-01-10" in out  # January Q4 payment belongs to 2025

    cli("reconcile", "--year", "2025")
    status = cli("status", "--year", "2025", "--all")
    assert "Food Bank $300.00" in status

    ack = tmp_path / "foodbank-ack.pdf"
    ack.write_bytes(
        make_pdf(
            [
                "Food Bank",
                "Thank you for your gift of $300 on December 1, 2025.",
                "No goods or services were provided. Tax year 2025",
            ]
        )
    )
    doc_id = cli("ingest", ack).split(":")[0]
    cli("link", doc_id, claim_id)
    cli("link", doc_id, claim_id, ok=False)  # already linked
    cli("reconcile", "--year", "2025")
    assert "Food Bank $300.00" not in cli("status", "--year", "2025", "--all")

    finding = "R-PAY-01:2025:federal"
    cli("ack", finding, "--note", "CPA confirmed", "--year", "2025")
    assert finding not in cli("status", "--year", "2025", "--all")

    wrens = "2025/donations/2025-02-26.friends-of-the-marsh-wrens"
    cli("waive", wrens, "written_acknowledgment", "--reason", "other", "--note", "letter lost")
    cli("waive", wrens, "nope", "--reason", "other", "--note", "x", ok=False)
    cli("reconcile", "--year", "2025")
    assert "Friends of the Marsh Wrens $2,000.00" not in cli("status", "--year", "2025", "--all")

    exp = "2025/copperfield-collective.pikeway-inv-partners.k1"
    assert "requires applicability_note" in cli("applicability", "set", exp, "not_issued", ok=False)
    cli("applicability", "set", exp, "not_issued", "--note", "Sponsor: no K-1 for 2025")


def test_friendly_errors(tmp_path: Path) -> None:
    assert "run `taxbinder init`" in cli("status", ok=False)
    cli("init")
    assert "already exists" in cli("init", ok=False)
    assert "indexed 0 records" in cli("reindex")  # empty vault


def test_recovery_key_restores_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from taxbinder.store import keys as keys_module

    out = cli("init")
    recovery = next(line.strip() for line in out.splitlines() if "AGE-SECRET-KEY-" in line)
    assert "status" not in cli("status", "--year", "2025")  # opens with the Keychain key

    monkeypatch.setattr(keys_module, "_MEMORY", keys_module.MemoryKeys())  # a new Mac
    assert "taxbinder restore-key" in cli("status", ok=False)
    wrong = runner.invoke(app, ["restore-key"], input="AGE-SECRET-KEY-1WRONG\n")
    assert wrong.exit_code != 0 and "doesn't open this vault" in wrong.output
    ok = runner.invoke(app, ["restore-key"], input=recovery + "\n")
    assert ok.exit_code == 0, ok.output
    cli("status", "--year", "2025")


def test_verify(tmp_path: Path) -> None:
    seed(tmp_path)
    exp = "2025/fernhollow-properties.kettle-410.k1"
    cli("reconcile", "--year", "2025")
    assert f"R-EXP-01:{exp}\n" in cli("status", "--year", "2025", "--all")
    assert "verified" in cli("verify", exp, "--note", "amounts match")
    cli("reconcile", "--year", "2025")
    assert f"R-EXP-01:{exp}\n" not in cli("status", "--year", "2025", "--all")
    assert "unverified" in cli("unverify", exp, "--note", "by mistake")
    cli("reconcile", "--year", "2025")
    assert f"R-EXP-01:{exp}\n" in cli("status", "--year", "2025", "--all")
    assert "2025/nope.k1" in cli("verify", "2025/nope.k1", "--note", "x", ok=False)


def test_year_and_rows(tmp_path: Path) -> None:
    seed(tmp_path)
    out = cli(
        "row",
        "add",
        "--year",
        "2025",
        "--institution",
        "Fidelis",
        "--source",
        "Rollover IRA",
        "--doc-type",
        "1099_r",
    )
    assert "added 2025/fidelis.rollover-ira.1099-r" in out
    assert "unknown document type" in cli(
        "row",
        "add",
        "--year",
        "2025",
        "--institution",
        "X",
        "--source",
        "Y",
        "--doc-type",
        "nope",
        ok=False,
    )
    assert "edited" in cli(
        "row", "edit", "2025/fidelis.rollover-ira.1099-r", "--amount", "7,000", "--comment", "IRA"
    )
    assert "give --amount" in cli("row", "edit", "2025/fidelis.rollover-ira.1099-r", ok=False)
    assert "deleted" in cli("row", "delete", "2025/fidelis.rollover-ira.1099-r")
    out = cli(
        "year", "new", "2026", "--from", "2025", "--drop", "2025/fernhollow-properties.cirrus.k1"
    )
    assert f"2026: {len(ROWS) - 1} rows copied from 2025" in out
    assert "dropped 2025/fernhollow-properties.cirrus.k1" in out
    assert "2026: nothing new to copy" in cli("year", "new", "2026")
    assert "errors" in cli("reconcile", "--year", "2026")
    assert "no rule parameters for 2031" in cli("year", "new", "2031", "--from", "2026") + cli(
        "reconcile", "--year", "2031", ok=False
    )


def test_history_and_undo(tmp_path: Path) -> None:
    seed(tmp_path)
    cli("row", "edit", "2025/fernhollow-properties.kettle-410.k1", "--comment", "oops")
    line = next(line for line in cli("history").splitlines() if "row edit" in line)
    assert "reconcile" not in cli("history")
    assert "undone: row edit" in cli("undo", line.split()[0])
    assert "no single change" in cli("undo", "zzzz", ok=False)


def test_privacy_redact_unredact(tmp_path: Path) -> None:
    cli("init")
    result = runner.invoke(
        app, ["privacy", "add", "name", "--note", "spouse"], input="Maria Example\n"
    )
    assert result.exit_code == 0 and "Maria" not in result.output
    assert "M… E…" in cli("privacy", "list")
    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(make_pdf(["Thank you, Maria Example", "SSN 900-22-4321"]))
    doc = cli("ingest", pdf).split("doc_")[1][:12]
    out = runner.invoke(app, ["redact", f"doc_{doc}"])
    assert out.exit_code == 0, out.output
    assert "Maria" not in out.stdout and "536-22" not in out.stdout and "[PERSON_01]" in out.stdout
    back = runner.invoke(app, ["unredact"], input="Ask [PERSON_01] about TIN_01.")
    assert "Maria Example" in back.stdout and "900-22-4321" in back.stdout


def test_across_the_years_cli(tmp_path: Path) -> None:
    seed(tmp_path)
    shown = cli("acrossyears", "show")
    assert shown.startswith("Type") and "2025" in shown
    from taxbinder import matrix
    from taxbinder.cli import open_store

    with open_store() as store:
        row = matrix.rows(store)[0]
    assert "2023: 1,200" in cli("acrossyears", "set", row.source.id, row.doc_type, "2023", "1200")
    assert "2023" in cli("acrossyears", "show")


def test_an_institution_nothing_uses_can_be_removed(tmp_path: Path) -> None:
    from taxbinder.models import Institution
    from taxbinder.store import RecordStore, keys_from_env, open_vault

    seed(tmp_path)
    with RecordStore(open_vault(tmp_path / "vault", keys_from_env())) as store:
        with store.operation("test", "a misread name left behind"):
            store.put(Institution(id="heronsgate-holdngs", name="Heronsgate Holdngs"), None)
    out = cli("institution", "list")
    assert "Heronsgate Holdngs  (nothing uses it)" in out
    assert "Fernhollow properties  (accounts (checklist rows) are under it)" in out
    assert "can't be removed: accounts" in cli("institution", "remove", "tidemark", ok=False)
    assert "removed Heronsgate Holdngs" in cli("institution", "remove", "heronsgate-holdngs")
    assert "heronsgate-holdngs" not in cli("institution", "list")
    cli("undo", cli("history").split()[0])  # one change: undone, it's back
    assert "heronsgate-holdngs" in cli("institution", "list")
