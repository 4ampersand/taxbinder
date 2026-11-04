"""The document viewer: pictures of a document's pages, never the file itself."""

from __future__ import annotations

import io

from pdfgen import make_pdf
from PIL import Image
from test_check import drop
from test_web import _upload, client, state  # noqa: F401 - `state` is a fixture

from taxbinder.web.app import AppState

K1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties"], ["Page two"])


def test_a_pdf_page_by_page(state: AppState) -> None:  # noqa: F811
    c = client(state)
    doc_id = _upload(c, K1, "scan_0001.pdf").json()["doc_id"]
    info = c.get(f"/documents/{doc_id}/view").json()
    assert info["pages"] == [[612, 792]] * 2 and info["name"] == "scan_0001.pdf"
    r = c.get(f"/documents/{doc_id}/page/2")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"  # never kept on disk, unencrypted
    assert Image.open(io.BytesIO(r.content)).size[0] > 1000  # sharp enough to read
    assert c.get(f"/documents/{doc_id}/page/3").status_code == 404
    assert c.get(f"/documents/{doc_id}/page/0").status_code == 404
    assert c.get("/documents/doc_000000000000/view").status_code == 404


def test_a_photo_is_one_page_upright(state: AppState) -> None:  # noqa: F811
    c = client(state)
    out = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(out, "JPEG")
    doc_id = _upload(c, out.getvalue(), "photo.jpg").json()["doc_id"]
    assert c.get(f"/documents/{doc_id}/view").json()["pages"] == [[300, 400]]
    png = c.get(f"/documents/{doc_id}/page/1").content
    assert Image.open(io.BytesIO(png)).size == (300, 400)


def test_a_saved_web_page_is_never_shown(state: AppState) -> None:  # noqa: F811
    c = client(state)
    page = b"<!doctype html><html><script>alert(1)</script></html>"
    doc_id = _upload(c, page, "portal page.html").json()["doc_id"]
    assert c.get(f"/documents/{doc_id}/view").json()["pages"] == []
    assert c.get(f"/documents/{doc_id}/page/1").status_code == 404


def test_view_from_across_the_years_documents_and_check(state: AppState) -> None:  # noqa: F811
    c = client(state)
    k1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    doc_id = _upload(c, k1, "k1.pdf").json()["doc_id"]  # on the Kettle row's 2025
    assert f'data-view="{doc_id}"' in c.get("/documents").text
    waiting = drop(state.store, "statement.pdf", make_pdf(["Form 1099-INT 2025", "Tidemark"]))
    assert f'data-view="{waiting}" aria-label="View: ' in c.get("/check").text
    mx = c.get("/years").text
    assert f'data-view="{doc_id}" data-page="1" aria-label="View the 2025 document' in mx
    assert 'id="viewer"' in mx  # one viewer on every page


def test_pages_drawn_and_read_at_once() -> None:
    """PDFium may be used by one thread at a time: pages drawn for the viewer while documents are
    read in the background must not fail (or corrupt memory, which crashed the app)."""
    from concurrent.futures import ThreadPoolExecutor

    from taxbinder import viewer
    from taxbinder.pdfdoc import Pdf

    pdfs = [make_pdf([f"Form 1099-INT {n}", "Payer"], ["Page two"]) for n in range(4)]

    def work(n: int) -> None:
        for _ in range(15):
            for data in pdfs:
                if n % 2:
                    assert viewer.page(data, "application/pdf", 2) is not None
                else:
                    pdf = Pdf(data, flatten=True)
                    try:
                        assert "1099-INT" in pdf.page(0).text
                    finally:
                        pdf.close()

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(work, range(6)))
