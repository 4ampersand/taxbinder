"""Preparer package (§13.1) from the fixture plus synthetic documents."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber
import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build, build_at
from typer.testing import CliRunner

from taxbinder.cli import app
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models.common import Link
from taxbinder.package.build import build_package, scrub
from taxbinder.store import RecordStore

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
WRENS = "2025/donations/2025-02-26.friends-of-the-marsh-wrens"

DOCS = {
    "k1.pdf": ["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"],
    "k1-amended.pdf": [
        "Schedule K-1 (Form 1065) 2025",
        "Fernhollow properties",
        "Kettle 410",
        "AMENDED K-1",
    ],
    "ms.pdf": ["Brackley Wealth", "2025 Form 1099-B", "Account Number: XXX-XX7731"],
    "w2.pdf": ["Form W-2 Wage and Tax Statement 2025", "Linden Education", "Jordan"],
    "ack.pdf": ["Friends of the Marsh Wrens", "Thank you for your gift. Tax year 2025"],
    "old-1099.pdf": ["Brackley Wealth", "2024 Form 1099-B"],  # other year: not in the package
    "scan.pdf": ["2025 statement 123456789"],  # unclassified but for 2025
}


@pytest.fixture
def store(tmp_path: Path):
    vault = new_vault(tmp_path / "vault")
    with RecordStore(vault) as s:
        build(s, "test")
        ids = {}
        for name, lines in DOCS.items():
            path = vault.inbox / name
            path.write_bytes(make_pdf(lines))
            ids[name] = ingest_file(s, path, "upload", now=NOW).doc_id
        claim, rev = s.get("claim", WRENS)  # link the acknowledgment by hand
        link = Link(doc=ids["ack.pdf"], linked_at=NOW, linked_by="test")
        with s.operation("test", "link ack"):
            s.put(claim.model_copy(update={"evidence": [link]}), rev)
        s.ids = ids  # type: ignore[attr-defined]
        yield s


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open()))


def test_package_layout_and_documents(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "2025-package"
    result = build_package(store, 2025, out, "test", now=NOW)

    assert sorted(p.name for p in out.iterdir()) == [
        "00-Returns",
        "00-summary.pdf",
        "01-W2",
        "02-1099",
        "03-K1",
        "04-1098-5498",
        "05-Estimated-Payments",
        "06-Donations",
        "07-Property-Vehicle",
        "08-Foreign",
        "09-Other",
        "index.csv",
        "open-items.csv",
    ]
    files = {str(p.relative_to(out)) for p in out.rglob("*.pdf") if p.name != "00-summary.pdf"}
    assert (
        files
        == {
            "03-K1/Fernhollow properties - Kettle 410 - K1.pdf",  # the amended one; the original is superseded
            "02-1099/Brackley Wealth - Consolidated Tax Statement - 1099.pdf",
            "01-W2/Linden Education - Jordan - W-2.pdf",
            "06-Donations/Donation - Friends of the Marsh Wrens - Receipt.pdf",
            "09-Other/scan - Document.pdf",
        }
    )
    assert result.files["03-K1"] == 1 and not result.unreadable
    k1 = (out / "03-K1/Fernhollow properties - Kettle 410 - K1.pdf").read_bytes()
    assert k1 == make_pdf(DOCS["k1-amended.pdf"])  # decrypted, byte for byte


def test_no_account_numbers_in_filenames_or_index(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    build_package(store, 2025, out, "test", now=NOW)
    for p in out.rglob("*"):
        assert not re.search(r"\d{5,}", p.name), p.name
    index = (out / "index.csv").read_text()
    numbers = [i.number for src in store.list("source") for i in src.identifiers]
    assert not any(n in index or n.replace(" ", "") in index for n in numbers)
    ms = next(r for r in read_csv(out / "index.csv") if r["doc_type"] == "1099_composite")
    assert ms["account"] == "…7731" and ms["linked_to"].endswith(
        "consolidated-tax-statement-7731.1099"
    )


def test_index_links_amounts_and_supersession(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    build_package(store, 2025, out, "test", now=NOW)
    rows = {r["document"]: r for r in read_csv(out / "index.csv")}
    assert len(rows) == 5
    k1 = rows[store.ids["k1-amended.pdf"]]
    assert k1["supersedes"] == store.ids["k1.pdf"] and k1["status"] == "vaulted/unreviewed/stale"
    ack = rows[store.ids["ack.pdf"]]
    assert ack["linked_to"] == WRENS and ack["amount"] == "2000"
    w2 = rows[store.ids["w2.pdf"]]
    assert w2["amount"] == "61250"  # the checklist's summary figure for that W-2


def test_open_items_match_the_findings(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    result = build_package(store, 2025, out, "test", now=NOW)
    findings, _ = store.get("findings", "2025")
    open_ids = {f.id for f in findings.findings if f.status == "open"}
    items = read_csv(out / "open-items.csv")
    assert {r["finding"] for r in items} == open_ids and result.open_items == len(open_ids)
    assert "R-PAY-01:2025:federal" in open_ids
    # The linked acknowledgment satisfies the Marsh Wrens gift's requirement.
    assert not any(r["finding"].startswith(f"R-DON-01:{WRENS}") for r in items)


def test_schedule_fbar_and_memo(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    build_package(store, 2025, out, "test", now=NOW)
    schedule = read_csv(out / "05-Estimated-Payments/schedule.csv")
    assert len(schedule) == 6 and schedule[0]["jurisdiction"] == "CA"
    fbar = read_csv(out / "08-Foreign/fbar-summary.csv")
    assert len(fbar) == 3 and fbar[0]["currency"] == "EUR" and fbar[0]["max_usd"]
    with pdfplumber.open(io.BytesIO((out / "00-summary.pdf").read_bytes())) as pdf:
        text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "Tax year 2025: document package" in text
    assert "federal payments total $36,000.00" in text
    assert "Corrected documents" in text and "k1-amended.pdf" in text
    assert "not_applicable" in text  # applicability decisions for the CPA to confirm


def test_refuses_to_overwrite(store: RecordStore, tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    out.mkdir()
    with pytest.raises(FileExistsError):
        build_package(store, 2025, out, "test", now=NOW)


def test_scrub() -> None:
    assert scrub("tidemark_Trust-258-000006") == "tidemark Trust-258"
    assert scrub("K-1 2025 (final)") == "K-1 2025 (final)"
    assert scrub("Account 12345678 - Statement") == "Account - Statement"
    assert scrub("a/b:c") == "a b c"


def test_cli_package_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    build_at(tmp_path / "vault")
    out = tmp_path / "pkg"
    r = runner.invoke(app, ["package", "build", "--year", "2025", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "wrote 0 documents" in r.output and "not encrypted" in r.output
    again = runner.invoke(app, ["package", "build", "--year", "2025", "--out", str(out)])
    assert again.exit_code != 0 and "already exists" in again.output
