"""Scans and photos read on the computer: text recognition inside the app, then
the same reading as a text PDF."""

from __future__ import annotations

import logging
import shutil
import struct
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from helpers import new_vault
from quills import build
from samples.generate import photo, scan
from test_parties import form

from taxbinder import ocr
from taxbinder.extract.figures import read_boxes
from taxbinder.ingest.inbox import ingest_file, rematch, scanned_pages
from taxbinder.institutions import find
from taxbinder.models import Institution
from taxbinder.models.registry import Identifier
from taxbinder.pdfdoc import Box, Page, Word
from taxbinder.redact import bundle
from taxbinder.slack import close, digits
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="the recognition models aren't installed")
TAMARACK = form("Tamarack Savings Bank", "88120-3304", recipient="Avery Quill")


def test_look_alikes_for_known_values_only() -> None:
    assert close("Tarnarack Savings Bank", "Tamarack Savings Bank")
    assert close("Tamarack 5avings 8ank", "Tamarack Savings Bank")
    assert not close("Gannet Home Lending", "Tamarack Savings Bank")
    assert digits("88120-33O4") == "88120-3304" and digits("HSA441O982") == "HSA4410982"
    known = [Institution(id="tamarack", name="Tamarack Savings Bank")]
    assert find("Tarnarack Savings Bank", None, known, scanned=True) == "tamarack"
    assert find("Tarnarack Savings Bank", None, known) is None  # a text PDF is read exactly
    assert Identifier(number="88120-3304").matches(digits("…33O4"))
    # never for amounts: a figure with a look-alike in it isn't read at all
    caption = [Word(w, Box(40 + 30 * i, 100, 68 + 30 * i, 108), 0) for i, w in
               enumerate(["1", "Interest", "income"])]  # fmt: skip
    page = Page(
        1,
        612,
        792,
        "1 Interest income\n1,7O6.29",
        [*caption, Word("1,7O6.29", Box(60, 120, 110, 130), 1)],
    )
    assert read_boxes(b"", "1099_int", ["1"], [page]) == {}


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s, "test")
    yield s
    s.close()


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


@needs_ocr
def test_a_scan_is_read_like_a_text_pdf(
    store: RecordStore, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    doc_id = drop(store, "scan.pdf", scan(TAMARACK))
    doc = store.get("document", doc_id)[0]
    c = doc.classification
    assert doc.scanned and (c.doc_type, c.tax_year) == ("1099_int", 2025)
    assert store.get("institution", c.issuer)[0].name == "Tamarack Savings Bank"
    assert c.account_number.endswith("3304") and doc.recipient.taxpayers == ["primary"]
    assert c.confidence <= 0.75  # always looked at on review
    cache = next(store.vault.root.rglob(f"{doc.ocr}*"))
    assert cache.suffix == ".age" and b"Tamarack" not in cache.read_bytes()  # encrypted
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("taxbinder"))
    assert "Tamarack" not in ours and "3304" not in ours
    monkeypatch.setattr(ocr, "read", lambda *a: pytest.fail("read again"))
    assert scanned_pages(store, doc)[0].text  # from the vault's cache
    rematch(store, "test")  # uses the cache too


def _with_orientation(jpeg: bytes, orientation: int) -> bytes:
    """The JPEG with an EXIF block saying how the camera was held."""
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">HHHIHH", 1, 0x0112, 3, 1, orientation, 0)
    tiff += b"\x00\x00\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


def _sideways(data: bytes) -> bytes:
    """A photo taken with the phone on its side: the page lies at 90° in the picture."""
    src, work = pymupdf.open(stream=data), pymupdf.open()
    page = work.new_page(width=900, height=700)
    page.show_pdf_page(pymupdf.Rect(54, 44, 846, 656), src, 0, rotate=90)
    jpeg = page.get_pixmap(dpi=110).tobytes("jpg", jpg_quality=80)
    src.close()
    work.close()
    return jpeg


@needs_ocr
def test_photos_upright_and_heic(store: RecordStore, tmp_path: Path) -> None:
    no_account = photo(form("Ambergate Property Management", ein="00-0000112"))
    assert store.get("document", drop(store, "IMG_0.jpg", no_account))[0].scanned
    doc = store.get("document", drop(store, "IMG_1.jpg", photo(TAMARACK)))[0]
    assert doc.scanned and doc.mime == "image/jpeg" and doc.original_filename == "IMG_1.jpg"
    assert doc.classification.doc_type == "1099_int"
    upright = [
        ocr.read_photo(_with_orientation(_sideways(TAMARACK), o))[0].text for o in (6, 8)
    ]  # one of the two turns the page the right way up
    assert any("Tamarack Savings Bank" in t for t in upright)
    if shutil.which("sips"):  # macOS's own image tool makes a HEIC, as an iPhone does
        jpg, heic = tmp_path / "p.jpg", tmp_path / "p.heic"
        jpg.write_bytes(photo(TAMARACK))
        subprocess.run(["sips", "-s", "format", "heic", jpg, "--out", heic], check=True,
                       capture_output=True)  # fmt: skip
        doc = store.get("document", drop(store, "IMG_2.heic", heic.read_bytes()))[0]
        assert doc.mime == "image/heic" and doc.classification.doc_type == "1099_int"


@needs_ocr
def test_chat_export_and_documents_page(store: RecordStore) -> None:
    from test_web import client

    doc_id = drop(store, "scan.pdf", scan(TAMARACK))
    b = bundle(store, [doc_id], "test")
    assert b.documents == 0 and "can't be checked for personal details" in b.refused[0][1]
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    page = client(state).get("/documents?year=2025").text
    assert ">scanned</span>" in page and "Tamarack Savings Bank" in page


@needs_ocr
def test_figures_from_a_scan() -> None:
    pages = ocr.read(scan(TAMARACK), "application/pdf")
    assert read_boxes(b"", "1099_int", ["1"], pages)["1"].value == Decimal("1766.29")
    assert ocr.read(TAMARACK, "application/pdf") == []  # a page with text is never OCR'd
