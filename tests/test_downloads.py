"""Downloading documents from the vault: one file, a zip, or saved into this Mac's Downloads."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from pdfgen import make_pdf
from test_web import (  # noqa: F401 - `state` is a fixture
    KETTLE,
    ORIGIN,
    _upload,
    client,
    row_of,
    rows_of,
    state,
)

from taxbinder.web.app import AppState

K1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])


def test_one_document_is_an_attachment_with_a_readable_name(state: AppState) -> None:  # noqa: F811
    c = client(state)
    doc_id = _upload(c, K1, "scan_0001.pdf").json()["doc_id"]
    r = c.get(f"/documents/{doc_id}/file")
    assert r.status_code == 200 and r.content == K1
    disposition = r.headers["content-disposition"]
    assert (
        disposition.startswith("attachment;")
        and "Fernhollow properties - Kettle 410 - K1.pdf" in disposition
    )
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/documents/doc_000000000000/file").status_code == 404
    assert c.get("/documents/..%2F..%2Fetc/file").status_code == 404


def test_saved_web_pages_are_never_shown_inside_the_app(state: AppState) -> None:  # noqa: F811
    c = client(state)
    page = b"<!doctype html><html><script>alert(1)</script></html>"
    doc_id = _upload(c, page, "portal page.html").json()["doc_id"]
    r = c.get(f"/documents/{doc_id}/file")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in r.headers["content-security-policy"]


def test_zip_of_selected_and_of_a_year(state: AppState) -> None:  # noqa: F811
    c = client(state)
    a = _upload(c, K1, "k1.pdf").json()["doc_id"]
    b = _upload(c, make_pdf(["statement"]), "Statement_0012345678.pdf").json()["doc_id"]
    r = c.get(f"/documents/zip?ids={a},{b},{a}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert (
        names[0] == "Fernhollow properties - Kettle 410 - K1.pdf" and len(names) == 2
    )  # no repeats
    assert not any(re.search(r"\d{5,}", n) for n in names)  # account-like digits removed
    year = zipfile.ZipFile(io.BytesIO(c.get("/documents/zip?year=2025").content)).namelist()
    assert year == ["Fernhollow properties - Kettle 410 - K1.pdf"]  # files attached to 2025 rows
    assert c.get("/documents/zip?ids=doc_000000000000").status_code == 404
    assert c.get("/documents/zip").status_code == 404


def test_app_window_saves_into_downloads(state: AppState, tmp_path: Path) -> None:  # noqa: F811
    state.downloads_dir = tmp_path / "Downloads"
    c = client(state)
    a = _upload(c, K1, "k1.pdf").json()["doc_id"]
    b = _upload(c, make_pdf(["other"]), "other.pdf").json()["doc_id"]
    first = c.post("/api/documents/save", json={"ids": [a]}, headers=ORIGIN).json()
    second = c.post("/api/documents/save", json={"ids": [a]}, headers=ORIGIN).json()
    assert (first["name"], second["name"]) == (
        "Fernhollow properties - Kettle 410 - K1.pdf",
        "Fernhollow properties - Kettle 410 - K1 (2).pdf",
    )  # never overwrites
    assert (state.downloads_dir / first["name"]).read_bytes() == K1
    both = c.post("/api/documents/save", json={"ids": [a, b]}, headers=ORIGIN).json()
    assert both["name"].endswith(".zip") and both["count"] == 2
    year = c.post("/api/documents/save", json={"year": 2025}, headers=ORIGIN).json()
    assert year["name"] == "TaxBinder 2025 documents.zip"
    bad = c.post("/api/documents/save", json={"ids": []}, headers=ORIGIN)
    assert bad.status_code == 400


def test_download_buttons_on_the_pages(state: AppState) -> None:  # noqa: F811
    c = client(state)
    doc_id = _upload(c, K1, "k1.pdf").json()["doc_id"]
    docs = c.get("/documents").text
    assert f'data-ids="{doc_id}"' in docs and 'id="dl-selected"' in docs
    kettle_row = row_of(rows_of(c, state), KETTLE)  # in the square's panel
    assert f'data-act="download" data-ids="{doc_id}"' in kettle_row
    home = c.get("/").text
    assert 'class="link small dl-year" data-year="2025">Download all files for 2025' in home
    assert 'data-remote="false"' in home
