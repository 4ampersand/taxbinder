"""Document ingestion (§6) with synthetic PDFs on a vault seeded from the fixture."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build

from taxbinder.ingest.inbox import ingest_file, scan_inbox, settled_files
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import BlobStore, RecordStore, fsck

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
KETTLE = "2025/fernhollow-properties.kettle-410.k1"


@pytest.fixture
def store(tmp_path: Path):
    with RecordStore(new_vault(tmp_path / "vault")) as s:
        build(s, "test")
        yield s


@pytest.fixture
def blobs(store: RecordStore) -> BlobStore:
    return store.blobs


def drop(store: RecordStore, name: str, *pages: list[str], portal: bool = False) -> Path:
    folder = store.vault.inbox / ("portal" if portal else "")
    path = folder / name
    path.write_bytes(make_pdf(*pages))
    old = time.time() - 10
    os.utime(path, (old, old))  # settled
    return path


def ingest(store: RecordStore, blobs: BlobStore, path: Path, via: str = "drop_folder"):  # noqa: ANN201
    return ingest_file(store, path, via, now=NOW)


K1 = [
    "Schedule K-1 (Form 1065) 2025",
    "Partnership: Fernhollow properties",
    "Kettle 410 LLC",
    "Final K-1",
]


def test_k1_linked_by_fund_name(store: RecordStore, blobs: BlobStore) -> None:
    path = drop(store, "k1.pdf", K1)
    result = ingest(store, blobs, path)
    assert result.linked_to == KETTLE
    assert not path.exists()  # removed only after it was stored

    doc, _ = store.get("document", result.doc_id)
    c = doc.classification
    assert (c.doc_type, c.tax_year, c.issuer, c.source) == (
        "k1_1065",
        2025,
        "fernhollow-properties",
        "fernhollow-properties.kettle-410",
    )
    exp, _ = store.get("expectation", KETTLE)
    assert exp.acquisition == "vaulted"
    assert [(link.doc, link.linked_by) for link in exp.evidence] == [(doc.id, "auto:matcher")]
    assert blobs.get(doc.sha256).startswith(b"%PDF")
    assert fsck(store.vault) == []


def test_1099_linked_by_masked_account_number(store: RecordStore, blobs: BlobStore) -> None:
    path = drop(
        store,
        "ms-2025.pdf",
        [
            "Brackley Wealth",
            "2025 Form 1099-B Consolidated Tax Statement",
            "Account Number: XXX-XX7731",
        ],
    )
    result = ingest(store, blobs, path)
    assert result.linked_to == "2025/brackley-wealth.consolidated-tax-statement-7731.1099"


def test_duplicate_adds_acquisition(store: RecordStore, blobs: BlobStore) -> None:
    first = ingest(store, blobs, drop(store, "k1.pdf", K1))
    again = ingest(store, blobs, drop(store, "k1 (1).pdf", K1, portal=True), via="portal_window")
    assert again.duplicate and again.doc_id == first.doc_id
    doc, _ = store.get("document", first.doc_id)
    assert [a.filename for a in doc.reacquired] == ["k1 (1).pdf"]
    exp, _ = store.get("expectation", KETTLE)
    assert len(exp.evidence) == 1


def test_unknown_account_number_yields_r_src_04(store: RecordStore, blobs: BlobStore) -> None:
    path = drop(
        store,
        "tidemark.pdf",
        ["TideMark Tidemark", "2025 Form 1099-B", "Account Number: XXXX-4321"],
    )
    result = ingest(store, blobs, path)
    assert result.linked_to is None and len(result.candidates) > 1
    findings = reconcile(store, 2025, "test", now=NOW).findings.findings
    [f] = [f for f in findings if f.rule == "R-SRC-04"]
    assert f.subjects == [result.doc_id] and "…4321" in f.message and "4321" not in f.id


def test_corrected_form_supersedes(store: RecordStore, blobs: BlobStore) -> None:
    first = ingest(store, blobs, drop(store, "k1.pdf", K1))
    exp, rev = store.get("expectation", KETTLE)
    with store.operation("avery", "verify"):
        store.put(exp.model_copy(update={"review": "verified"}), rev)

    corrected = ingest(store, blobs, drop(store, "k1-amended.pdf", [*K1, "AMENDED K-1"]))
    assert corrected.superseded == first.doc_id
    doc, _ = store.get("document", corrected.doc_id)
    assert doc.version.supersedes == first.doc_id and doc.version.is_corrected
    exp, _ = store.get("expectation", KETTLE)
    assert [(link.doc, link.role) for link in exp.evidence] == [
        (first.doc_id, "superseded"),
        (corrected.doc_id, "primary"),
    ]
    assert (exp.review, exp.delivery) == ("unreviewed", "stale")  # imported as sent
    ids = {f.id for f in reconcile(store, 2025, "test", now=NOW).findings.findings}
    assert f"R-EXP-04:{KETTLE}:{corrected.doc_id}" in ids
    assert f"R-EXP-07:{KETTLE}" in ids


def test_portal_download_metadata(store: RecordStore, blobs: BlobStore) -> None:
    path = drop(
        store,
        "corvina.pdf",
        ["Schedule K-1 (Form 1065) 2025", "Corvina Ledger Fund"],
        portal=True,
    )
    meta = path.with_name(path.name + ".meta.json")
    meta.write_text(json.dumps({"source_url": "https://portal.example.com/doc/1?token=SECRET"}))
    [result] = scan_inbox(store)
    doc, _ = store.get("document", result.doc_id)
    assert doc.acquired.via == "portal_window"
    assert doc.acquired.source_url == "https://portal.example.com/doc/1"
    assert not meta.exists()
    assert "SECRET" not in (store.vault.records / f"documents/{doc.id}.yaml").read_text()
    # Corvina was "received, not vaulted" in the test household; now it's vaulted.
    assert result.linked_to == "2025/copperfield-collective.corvina-ledger-fund.k1"
    ids = {
        f.id
        for f in reconcile(store, 2025, "test", now=NOW).findings.findings
        if f.status == "open"
    }
    assert "R-EXP-02:2025/copperfield-collective.corvina-ledger-fund.k1" not in ids


def test_html_page_is_stored_unclassified(store: RecordStore, blobs: BlobStore) -> None:
    path = store.vault.inbox / "login.html"
    path.write_bytes(b"<!DOCTYPE html><html><body>Please sign in</body></html>")
    result = ingest(store, blobs, path)
    doc, _ = store.get("document", result.doc_id)
    assert doc.mime == "text/html" and doc.classification.doc_type is None
    assert result.linked_to is None


def test_only_settled_files_are_picked_up(store: RecordStore) -> None:
    inbox = store.vault.inbox
    ready = drop(store, "ready.pdf", K1)
    (inbox / "partial.pdf.crdownload").write_bytes(b"x")
    (inbox / ".hidden.pdf").write_bytes(b"x")
    fresh = inbox / "fresh.pdf"
    fresh.write_bytes(b"x")
    assert settled_files(inbox) == [(ready, "drop_folder")]
    assert settled_files(inbox, now=time.time() + 60)[0][0] == fresh


def test_failure_leaves_file_for_retry(
    store: RecordStore, blobs: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = drop(store, "k1.pdf", K1)

    def boom(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "put", boom)
    assert scan_inbox(store) == []
    assert path.exists()


# -- K-1s as they really look (§6.2 Match) ----------------------------------------------

IRS_BOILERPLATE = [
    "Department of the Treasury Internal Revenue Service",
    "For calendar year 2025, or tax year beginning ending",
    "See separate instructions. www.irs.gov/Form1065",
    "C IRS Center where partnership filed return: Ogden, UT",
    "Qualified Opportunity Fund investments - see IRS Sch. K-3 instructions",
]


def real_k1(entity: str) -> list[str]:
    """A K-1 page: names the partnership entity only, never the sponsor; IRS text throughout."""
    return ["Schedule K-1 (Form 1065) 2025", *IRS_BOILERPLATE, f"A Partnership's name: {entity}"]


@pytest.mark.parametrize(
    ("filename", "entity", "expected"),
    [
        (
            "K1 2025_MRAster.pdf",
            "Aster Easton Owner LLC",
            "fernhollow-properties.aster-apartments-easton",
        ),
        (
            "K1 2025_MRRidgeatCobbleFlats.pdf",
            "RCF Holdings LLC",
            "fernhollow-properties.ridge-at-cobble-flats",
        ),
        (
            "k1 2025_Corbel HOP.pdf",
            "CNL Hawksmoor LLC",
            "corbel-net-lease.hawksmoor-office-park-hop",
        ),
        ("K1 2025_Corbel TQA.pdf", "CNL Portfolio XIV LLC", "corbel-net-lease.tqa"),
        ("K1 2025_MRLattice.pdf", "FP Lattice Owner LLC", "fernhollow-properties.lattice"),
    ],
)
def test_real_world_k1s_link(
    store: RecordStore, blobs: BlobStore, filename: str, entity: str, expected: str
) -> None:
    result = ingest(store, blobs, drop(store, filename, real_k1(entity)))
    assert result.linked_to == f"2025/{expected}.k1"
    doc, _ = store.get("document", result.doc_id)
    assert doc.classification.issuer != "irs"  # IRS boilerplate never names the issuer


def test_boilerplate_and_short_words_do_not_link(store: RecordStore, blobs: BlobStore) -> None:
    # "Opportunity" (every K-1) must not pick "Opportunity Fund I"; a short key in the text only
    # ("lake" in an address) must not pick "Bayside Commons". Unclear → left for a person.
    text = [*real_k1("Unknown Holdings LP"), "Partnership address: 10 Lake St, Salt Lake City, UT"]
    result = ingest(store, blobs, drop(store, "scan_0001.pdf", text))
    assert result.linked_to is None


def test_rematch_links_unmatched_documents_and_keeps_links(
    store: RecordStore, blobs: BlobStore
) -> None:
    from taxbinder.ingest.inbox import rematch
    from taxbinder.years import add_row

    linked = ingest(store, blobs, drop(store, "k1.pdf", K1))
    orphan = ingest(
        store, blobs, drop(store, "K1 2025_Fidelis_Rollover.pdf", real_k1("Rollover Partners LP"))
    )
    assert linked.linked_to == KETTLE and orphan.linked_to is None
    add_row(store, 2025, "Fidelis", "Rollover", "k1_1065", "primary", "test")  # the missing row

    results = rematch(store, "test", now=NOW)
    assert [(r.doc_id, r.linked_to) for r in results] == [
        (orphan.doc_id, "2025/fidelis.rollover.k1")
    ]
    kettle, _ = store.get("expectation", KETTLE)
    assert [link.doc for link in kettle.evidence] == [linked.doc_id]  # never moved
    doc, _ = store.get("document", orphan.doc_id)
    assert doc.classification.source == "fidelis.rollover"
    assert rematch(store, "test", now=NOW) == []  # nothing left unlinked
    assert fsck(store.vault) == []
