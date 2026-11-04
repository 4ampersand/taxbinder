"""Copy diagnostic info: versions, counts and review corrections, recent
errors without their values; never a name, amount, account number or file name (the invented
household; PDFs made here)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from test_sharing import pair, shared  # noqa: F401 - `shared` is a fixture
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder import check, diagnostics, whose
from taxbinder.entities import create_entity, move_document
from taxbinder.entities import vault_path as entity_vault
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models.document import Recipient
from taxbinder.review import edit_document
from taxbinder.store import RecordStore, keys_from_env, open_vault
from taxbinder.web.app import LOCAL_ONLY, AppState

NAME = "Avery Quill 2025 W-2 acct 4471029384.pdf"


@pytest.fixture(autouse=True)
def fresh_errors():
    diagnostics.RECENT.items.clear()
    diagnostics.watch()
    yield
    diagnostics.RECENT.items.clear()


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "app" / "vault"))
    build(s, "test")
    yield s
    s.close()


def _doc(store: RecordStore, name: str, lines: list[str]) -> str:
    path = store.vault.inbox / name
    path.write_bytes(make_pdf(lines))
    return ingest_file(store, path, "upload", "test").doc_id


def test_corrections_are_counted_from_the_records(store: RecordStore, tmp_path: Path) -> None:
    w2 = _doc(store, "w2.pdf", ["Form W-2 Wage and Tax Statement 2025", "Linden Education"])
    k1 = _doc(store, "k1.pdf", ["Schedule K-1 (Form 1065) 2025", "Fernhollow properties"])
    int_ = _doc(store, "int.pdf", ["Form 1099-INT 2025", "Brackley Wealth", "Interest"])
    assert store.get("document", int_)[0].classification.issuer == "brackley-wealth"
    assert diagnostics.counts([store]).read_corrections == 0

    edit_document(store, w2, "test", "w2", 2025)  # the same as read: not a correction
    edit_document(store, k1, "test", "k1_1065", 2024)  # the year was wrong
    check.edit(store, int_, "test", issuer="Brackley Wealth")  # the same sender: no
    assert diagnostics.counts([store]).read_corrections == 1
    check.edit(store, int_, "test", issuer="Linden Education")  # who sent it was wrong
    c = diagnostics.counts([store])
    assert (c.documents, c.read_corrections, c.owner_corrections) == (3, 2, 0)

    # whose it is: an answer to "whose is this?" isn't a correction; changing it is
    doc, rev = store.get("document", w2)
    with store.operation("test", "asked"):
        store.put(doc.model_copy(update={"recipient": Recipient(printed="A Quill", ask=True)}), rev)
    whose.set_whose(store, w2, ["primary"], "test")
    assert diagnostics.counts([store]).owner_corrections == 0
    whose.set_whose(store, w2, ["spouse"], "test")
    assert diagnostics.counts([store]).owner_corrections == 1

    # moved to another filer: counted in the binder it went to
    slug = create_entity(store.vault.root, keys_from_env(), "Northwind Holdings Inc.", "1120", "t")
    with RecordStore(open_vault(entity_vault(store.vault.root, slug), keys_from_env())) as other:
        move_document(store, other, k1, "test")
        assert diagnostics.counts([store, other]).owner_corrections == 2


def test_errors_keep_the_sentence_never_the_values(store: RecordStore) -> None:
    log = logging.getLogger("taxbinder.ingest.inbox")
    log.warning("could not read %s", NAME)
    try:
        check.mark(store, NAME, "an answer TaxBinder doesn't know", "test")  # raises in taxbinder
    except ValueError:
        logging.getLogger("taxbinder.web").exception("ingest failed for an inbox file")
    logging.getLogger("elsewhere").error("not ours: %s", NAME)  # another library's: not kept
    text = diagnostics.report([store], [2], 1, False)
    assert "WARNING taxbinder.ingest.inbox: could not read %s" in text
    assert (
        "ERROR taxbinder.web: ingest failed for an inbox file (ValueError at taxbinder/check.py:"
        in text
    )
    assert "Avery" not in text and "4471029384" not in text and ".pdf" not in text
    assert "not ours" not in text


def test_the_text_holds_no_household_details(state: AppState) -> None:  # noqa: F811
    text = diagnostics.report([state.store], [2], 1, False)
    for line in ("TaxBinder diagnostic info", "macOS: ", "Binder format: 2 (this version opens",
                 "Filers: 1 (the household)", "Corrected after reading", "Home sharing: off",
                 "Recent warnings and errors"):  # fmt: skip
        assert line in text, line
    people = [t.name for t in state.store.list("taxpayer")]
    places = [i.name for i in state.store.list("institution")]
    assert not any(n in text for n in [*people, *places]) and "$" not in text
    assert diagnostics.mask("me@example.com ~/Library/x 900-45-6789 0012345678 2026-10-13 12:01") \
        == "…@… …/… … … 2026-10-13 12:01"  # fmt: skip


def test_the_page_and_its_button(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/help").text
    assert (
        'id="report"' in html and 'id="diag-show"' in html and "Read it before you send it" in html
    )
    r = c.get("/api/diagnostics", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["text"].startswith("TaxBinder diagnostic info")
    assert "Filers: 1" in r.json()["text"]
    assert "/api/diagnostics" in LOCAL_ONLY


def test_not_from_a_paired_phone(shared: AppState) -> None:  # noqa: F811
    phone = pair(shared)
    assert 'id="report"' not in phone.get("/help").text
    assert phone.get("/api/diagnostics").status_code in (403, 404)
