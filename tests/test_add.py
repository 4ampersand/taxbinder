"""Add documents: files queued as they arrive and read in the background; bad
files reported one by one; adding again or a restart mid-batch adds nothing twice (fictional
data, PDFs made in the test)."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from test_web import ORIGIN, PORT, client

from taxbinder import add
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState, create_app

FORMS = [
    ("Schedule K-1 (Form 1065) {y}", "Fernhollow properties", "Partner {n}"),
    ("Form 1099-INT {y}", "Larkmoor Savings Bank", "Account ending {n}"),
    ("Form 1099-DIV {y}", "Quarry Hill Funds", "Statement {n}"),
    ("Form W-2 Wage and Tax Statement {y}", "Brightwater Studio", "Control {n}"),
]


def _pdf(n: int, year: int) -> bytes:
    title, issuer, line = FORMS[n % len(FORMS)]
    return make_pdf([title.format(y=year), issuer, line.format(n=n)])


def _locked_pdf() -> bytes:
    doc = pymupdf.open(stream=_pdf(0, 2025), filetype="pdf")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")


def _photo(text: str) -> bytes:
    doc = pymupdf.open(stream=make_pdf([text]), filetype="pdf")
    return doc[0].get_pixmap(dpi=100).tobytes("png")


@pytest.fixture
def app(tmp_path: Path):
    store = RecordStore(new_vault(tmp_path / "vault"))
    build(store, "test")
    s = AppState(vault_path=store.vault.root, store=store, port=PORT, emit=lambda e: None)
    s.downloads_dir = tmp_path / "downloads"
    yield s
    s.store.close()


def _send(c, files: list[tuple[str, bytes]], batch: str) -> None:  # noqa: ANN001
    for name, data in files:
        r = c.post("/api/add", content=data,
                   headers=ORIGIN | {"X-Filename": name, "X-Batch": batch})  # fmt: skip
        assert r.status_code == 200, r.text


def _wait(c, timeout: float = 300) -> dict:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = c.get("/api/add/status").json()
        if not st["running"]:
            return st
        time.sleep(0.2)
    raise AssertionError("still reading")


def test_two_hundred_mixed_files(app: AppState) -> None:
    c = client(app)
    good = [(f"doc {n:03d}.pdf", _pdf(n, (2023, 2024, 2025)[n % 3])) for n in range(190)]
    photos = [(f"IMG_{n}.png", _photo(f"Donation receipt {n} Heron Trust")) for n in range(3)]
    bad = [("statements.pdf", _locked_pdf()), ("statements copy.pdf", _locked_pdf()),
           ("cut off.pdf", good[0][1][:400]), ("notes.pdf", b"just some text, renamed"),
           ("empty.pdf", b"")]  # fmt: skip
    again = [("doc 000 copy.pdf", good[0][1]), ("doc 001 again.pdf", good[1][1])]
    files = good[:100] + bad + photos + good[100:] + again
    assert len(files) == 200
    started = time.monotonic()
    _send(c, files, "first")
    assert time.monotonic() - started < 60  # taken in at once, not read one by one first
    t = time.monotonic()  # the app stays responsive while it reads
    assert c.get("/api/add/status").status_code == 200 and c.get("/").status_code == 200
    assert time.monotonic() - t < 5
    st = _wait(c)

    assert st["total"] == 200 and st["done"] == 200
    by_name = {i["name"]: i for i in st["items"]}
    assert len(by_name) == 200  # every file reported once
    assert by_name["statements.pdf"]["note"].startswith("has a password")
    assert by_name["cut off.pdf"]["note"] == add.DAMAGED
    assert by_name["notes.pdf"]["note"] == "isn't a PDF or a picture"
    assert by_name["empty.pdf"]["note"] == "is empty"
    assert st["bad"] == 5
    assert by_name["doc 000 copy.pdf"]["note"] == "already added, skipped"
    assert by_name["doc 003.pdf"]["note"].startswith("read · ")
    docs = app.store.list("document")
    assert len(docs) == 193  # the good ones and the photos; no copy twice
    years = {d.classification.tax_year for d in docs if d.classification}
    assert {2023, 2024, 2025} <= years  # any year, each sorted by the year it's for
    assert not add.pending(app.store.vault)  # the queue is empty
    html = c.get("/add").text
    assert 'id="fx"' in html and 'href="/check"' in html


def test_adding_again_and_a_restart_add_nothing_twice(app: AppState) -> None:
    c = client(app)
    files = [(f"doc {n}.pdf", _pdf(n, 2025)) for n in range(6)]
    _send(c, files, "one")
    _wait(c, 60)
    assert len(app.store.list("document")) == 6
    _send(c, files, "two")  # the same folder again: a new batch, all skipped
    st = _wait(c, 60)
    assert st["total"] == 6 and {i["note"] for i in st["items"]} == {"already added, skipped"}
    assert len(app.store.list("document")) == 6

    # The app quit with files still queued: the next start reads them, once.
    q = add.queue_dir(app.store.vault)
    for n, (name, data) in enumerate([files[0], ("new one.pdf", _pdf(99, 2025))], start=1):
        (q / f"{n:06d}").mkdir(parents=True)
        (q / f"{n:06d}" / name).write_bytes(data)
    (q / "000003").mkdir()
    (q / "000003" / ".half.pdf.part").write_bytes(b"%PDF-")  # cut off while it arrived
    c.app.state.reader.stop()  # the app quit
    fresh = create_app(app)
    fresh.state.reader.wake()
    end = time.monotonic() + 60
    while len(add.pending(app.store.vault)) or fresh.state.reader.batch.running:
        assert time.monotonic() < end
        time.sleep(0.1)
    items = {i.name: i for i in fresh.state.reader.batch.items}
    assert items["doc 0.pdf"].note == "already added, skipped"
    assert items["new one.pdf"].note.startswith("read")
    assert len(app.store.list("document")) == 7


def test_each_document_through_every_step(app: AppState) -> None:
    """Each document goes through every step before the next starts, and each step says
    what it found; without a model, the model's step says so; something unclear is asked."""
    from taxbinder.report import DOC_LABELS

    c = client(app)
    _send(c, [("k1.pdf", _pdf(0, 2025)), ("again.pdf", _pdf(0, 2025)),
              ("interest.pdf", _pdf(1, 2025)),
              ("receipt.png", _photo("Donation receipt Heron Trust"))], "steps")  # fmt: skip
    by_name = {i["name"]: i for i in _wait(c)["items"]}
    k1 = by_name["k1.pdf"]
    assert k1["steps"] == [*add.STEPS, "filed"]
    found = k1["found"]
    assert found["kind"] == DOC_LABELS["k1_1065"] and found["sender"] == "Fernhollow properties"
    assert found["year"] == 2025 and found["scan"] is False and found["asks"] == []
    assert found["model"] == {"state": "no model"} and "amounts" in found and "checks" in found
    assert by_name["again.pdf"]["steps"] == ["received", "reading"]  # a copy stops there
    assert found["sender_by"] == "the page"
    interest = by_name["interest.pdf"]  # its sender's name not read: matched to a row
    assert interest["found"]["sender_by"] == "its row" and interest["found"]["sender"]
    photo = by_name["receipt.png"]
    assert photo["found"]["scan"] is True and photo["steps"][-1] == "asked"
    assert {"what it is", "the year"} <= set(photo["found"]["asks"])
