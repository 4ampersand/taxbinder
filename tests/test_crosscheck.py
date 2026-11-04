"""Each form read twice and compared, with a stand-in for the model that answers what it's
told to: agreed, different, only the model, not on the page, a failed check, no answer."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from test_extract_forms import _w2
from test_matrix import _row
from test_years import store  # noqa: F401 - fixture

from taxbinder import llm, matrix, modelread
from taxbinder.engine import Unanswered
from taxbinder.extract import crosscheck
from taxbinder.extract.figures import read_boxes
from taxbinder.store import RecordStore


class Stand:
    """Answers each form with the rules' boxes, changed where told."""

    def __init__(self, change: dict | None = None, fail: bool = False) -> None:
        self.change, self.fail, self.asked = change or {}, fail, []

    def ask(self, messages: list, schema: dict) -> str:
        self.asked.append(messages)
        if self.fail:
            raise Unanswered("didn't answer in time")
        answer = {b: None for b in schema["required"]}
        answer |= {"1": 100000.0, "2": 15000.0, "3": 100000.0, "4": 6200.0, "5": 100000.0,
                   "6": 1450.0}  # fmt: skip
        return json.dumps(answer | self.change)


def test_the_readings_compared() -> None:
    model = Stand({"2": 15100.0, "7": 50.0, "16": 2.0})
    got = crosscheck.read(model, _w2(), year=2025)["forms"]["W2"]["boxes"]
    assert got["1"] == {"value": 100000.0, "page": 1, "status": "confirmed", "grounded": True}
    assert got["2"]["status"] == "disagree" and got["2"]["rule_value"] == 15000
    assert not got["2"]["grounded"]  # 15,100 isn't printed on the page: invented
    assert got["7"]["status"] == "llm" and not got["7"]["grounded"]
    assert got["8"]["status"] == "confirmed" and got["8"]["value"] is None  # both: empty
    messages = model.asked[0]
    assert messages[0]["role"] == "system" and "Report only what is printed" in messages[0]["content"]
    parts = messages[1]["content"]
    assert "tax form W2" in parts[0]["text"] and "Wages, tips" in parts[0]["text"]  # its text
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    wrong = crosscheck.read(Stand({"4": 9999.0}), _w2(), year=2025)["forms"]["W2"]["boxes"]
    assert wrong["4"]["status"] == "check-failed" and wrong["4"]["check"] == "4 = 6.2% of 3"

    failed = crosscheck.read(Stand(fail=True), _w2())["forms"]["W2"]
    assert failed["model_error"] == "Unanswered"  # the rules' values, marked
    assert failed["boxes"]["1"]["status"] == "rules-only" and failed["boxes"]["1"]["value"] == 100000


def test_across_the_years_takes_the_agreed_and_asks_about_the_rest() -> None:
    reading = crosscheck.read(Stand({"2": 15100.0, "6": 1460.0}), _w2(), year=2025)
    reading["forms"]["W2"]["boxes"]["6"]["grounded"] = True  # as if 1,460 were on the page
    got = read_boxes(_w2(), "w2", ["1", "2", "6", "8"], year=2025, model=reading)
    assert got["1"].value == Decimal("100000") and got["1"].check == ""
    # not on the page: the rules' value stays, and why it needs a look
    assert got["2"].value == Decimal("15000")
    assert "the rules read 15,000, the AI model 15,100" in got["2"].check
    assert "isn't printed on the page" in got["2"].check
    assert got["6"].value == Decimal("1450") and "the rules read 1,450, the AI model 1,460" in got["6"].check
    assert "6 = 1.45% of 5" in got["6"].check  # and it breaks the form's own check
    assert "8" not in got


def test_documents_read_in_the_background(store: RecordStore, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001, F811
    """The reader reads each document of a known form once per model, keeps what it read with the
    document (encrypted), and Across the years' suggestion comes from it."""
    from kformgen import form
    from test_years import KETTLE

    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.review import link_document
    from taxbinder.web.app import AppState

    k1 = form([(330, 100, 140, "1", "Ordinary business income (loss)", "10."),
               (470, 100, 130, "14", "Self-employment earnings (loss)", ""),
               (330, 130, 140, "2", "Net rental real estate income (loss)", "(500.)"),
               (470, 130, 130, "15", "Credits", "")],
              ["Schedule K-1 (Form 1065) 2025", "OMB No. 1545-0123"])  # fmt: skip
    exp, _ = store.get("expectation", KETTLE)
    key = f"{exp.source}|{exp.doc_type}"
    if key not in {r.key for r in matrix.rows(store)}:
        matrix.add_row(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_boxes(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "", "t")
    path = store.vault.inbox / "kettle-k1.pdf"
    path.write_bytes(k1)
    doc = ingest_file(store, path, "upload", "t").doc_id
    if not any(x.doc == doc for x in store.get("expectation", KETTLE)[0].evidence):
        link_document(store, doc, KETTLE, "t")

    class K1Stand:
        asked = 0

        def ask(self, messages: list, schema: dict) -> str:
            K1Stand.asked += 1
            return json.dumps({b: None for b in schema["required"]} | {"1": 10.0, "2": -600.0})

    state = AppState(vault_path=store.vault.root, store=store)
    tiny = llm.catalogue(tmp_path)[0]
    monkeypatch.setattr(llm, "current", lambda where: tiny)
    reader = modelread.ModelReader(state)
    monkeypatch.setattr(reader, "_engine", lambda model: K1Stand())
    assert reader.work() >= 1
    d = store.get("document", doc)[0]
    assert d.model_read and modelread.reading(store, d)["model"] == tiny.id
    on_disk = store.blobs.find(d.model_read).read_bytes()
    assert b"model" not in on_disk and b"600" not in on_disk  # kept encrypted, like the document
    cell = _row(store, key).cells[2025]
    assert cell.suggested == [Decimal("10"), Decimal("-500")]  # the rules' -500 (the model's isn't printed)
    assert any("the rules read -500, the AI model -600" in c for c in cell.checks)
    asked = K1Stand.asked
    assert reader.work() == 0 and K1Stand.asked == asked  # read once per model


@pytest.mark.parametrize("value", [None, 0, [], ""])
def test_nothing_is_always_on_the_page(value: object) -> None:
    assert crosscheck.grounded(value, set(), set())


def _with_reading(store: RecordStore, doc_id: str, sender: str, year: int, printed: bool = True) -> None:  # noqa: F811
    doc, rev = store.get("document", doc_id)
    parties = {"sender": {"value": sender, "printed": printed},
               "tax_year": {"value": float(year), "printed": printed},
               "recipient": {"value": None, "printed": False}}  # fmt: skip
    blob = store.blobs.put(json.dumps({"model": "tiny", "forms": {}, "parties": parties}).encode())
    with store.operation("auto:model", f"model: read {doc_id}"):
        store.put(doc.model_copy(update={"model_read": blob.sha256}), rev)


def test_check_asks_when_the_model_reads_the_sender_otherwise(store: RecordStore) -> None:  # noqa: F811
    """A sender or year the model read differently, and printed on the document, is a
    question on Check (the rules' reading stays until it's answered); an unprinted one isn't."""
    from test_check import drop, k1

    from taxbinder import check

    doc = drop(store, "k1.pdf", k1(1))
    before = check.report(store)
    assert not any(f.doc.id == doc for f in before.asked)
    _with_reading(store, doc, "Kettle Creek Partners LP", 2024, printed=False)
    assert not any(f.doc.id == doc for f in check.report(store).asked)  # not on the page: ignored
    _with_reading(store, doc, "Kettle Creek Partners LP", 2024)
    asked = next(f for f in check.report(store).asked if f.doc.id == doc)
    assert asked.question == "read" and asked.suggest == "Kettle Creek Partners LP"
    assert asked.suggest_year == 2024 and "the AI model “Kettle Creek Partners LP”" in asked.note
    check.mark(store, doc, "read_ok", "t")  # keep what the rules read
    assert not any(f.doc.id == doc for f in check.report(store).asked)


def test_the_owners_check_counts_only(store: RecordStore) -> None:  # noqa: F811
    """The readers compared with what was typed, in counts, never values."""
    from kformgen import form
    from test_years import KETTLE

    from taxbinder.dev import reading_check
    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.review import link_document

    k1 = form([(330, 100, 140, "1", "Ordinary business income (loss)", "10."),
               (330, 130, 140, "2", "Net rental real estate income (loss)", "(500.)")],
              ["Schedule K-1 (Form 1065) 2025", "OMB No. 1545-0123"])  # fmt: skip
    exp, _ = store.get("expectation", KETTLE)
    key = f"{exp.source}|{exp.doc_type}"
    if key not in {r.key for r in matrix.rows(store)}:
        matrix.add_row(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_boxes(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "10 / -400", "t")
    path = store.vault.inbox / "kettle-k1.pdf"
    path.write_bytes(k1)
    doc = ingest_file(store, path, "upload", "t").doc_id
    if not any(x.doc == doc for x in store.get("expectation", KETTLE)[0].evidence):
        link_document(store, doc, KETTLE, "t")
    tally = reading_check.new_tally()
    reading_check.check_store(store, None, "", tally)
    out = reading_check.report(tally)
    assert "k1_1065: 1 documents (0 scanned)" in out
    rules = "rules: 2 of 2 boxes found, 1 documents with every box, 1 same as typed, 1 not as typed"
    assert rules in out
    assert "400" not in out and "500" not in out and "Kettle" not in out


def test_a_new_document_read_with_the_model_in_its_turn(store: RecordStore, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001, F811
    """Add documents waits for the model on each document; what it read, in counts."""
    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.web.app import AppState

    path = store.vault.inbox / "w2.pdf"
    path.write_bytes(_w2())
    doc = ingest_file(store, path, "upload", "t").doc_id
    reader = modelread.ModelReader(AppState(vault_path=store.vault.root, store=store))
    monkeypatch.setattr(llm, "current", lambda where: None)
    assert reader.read_now(store, doc) == {"state": "no model"}
    monkeypatch.setattr(llm, "current", lambda where: llm.catalogue(tmp_path)[0])
    monkeypatch.setattr(reader, "_engine", lambda model: Stand({"2": 15500.0}))
    got = reader.read_now(store, doc)
    assert got["state"] == "read" and got["boxes"] >= 6 and got["differ"] == 1
    assert got["agreed"] >= 5
    assert reader.read_now(store, doc) == {"state": "not needed"}  # read once per model
    assert reader.work() == 0

    def broken(*args: object) -> None:
        raise OSError("the engine went away")

    monkeypatch.setattr(crosscheck, "read", broken)
    monkeypatch.setattr(llm, "current", lambda where: llm.catalogue(tmp_path)[1])  # another model
    assert reader.read_now(store, doc) == {"state": "failed"}  # it's stored; tried again later


def test_the_reading_bench(store: RecordStore, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001, F811
    """For one document, the rules' reading, the model's, what's kept (what Across the years
    takes: an agreed box as it is, any other with the reason it needs a look)."""
    from taxbinder import bench
    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.web.app import AppState

    path = store.vault.inbox / "w2.pdf"
    path.write_bytes(_w2())
    doc = ingest_file(store, path, "upload", "t").doc_id
    before = bench.bench(store, doc)
    assert not before["model_read"] and before["model"] == []
    rules = {r["box"]: r["value"] for r in before["rules"]}
    assert rules["1"] == "100,000.00" and rules["2"] == "15,000.00"
    assert {r["box"]: r["label"] for r in before["rules"]}["2"] == "Federal income tax withheld"
    assert {k["box"]: k["why"] for k in before["kept"]}["2"] == ""  # the rules alone: taken

    reader = modelread.ModelReader(AppState(vault_path=store.vault.root, store=store))
    monkeypatch.setattr(llm, "current", lambda where: llm.catalogue(tmp_path)[0])
    monkeypatch.setattr(reader, "_engine", lambda model: Stand({"2": 15500.0}))
    reader.read_now(store, doc)
    after = bench.bench(store, doc)
    assert after["model_read"]
    model = {m["box"]: m for m in after["model"]}
    assert model["2"]["value"] == "15,500.00" and model["2"]["says"] == "differs from the rules; not printed on the page"
    assert model["1"]["says"] == "the same as the rules"
    kept = {k["box"]: k for k in after["kept"]}
    assert kept["1"]["why"] == "" and kept["2"]["value"] == "15,000.00"  # the rules' value shown
    assert "the rules read 15,000, the AI model 15,500" in kept["2"]["why"]
    assert all({"severity", "message", "rule"} <= c.keys() for c in after["checks"])


def test_check_asks_only_about_key_boxes_in_doubt(store: RecordStore, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001, F811
    """A key box the readers don't agree on is asked about, with the
    rules' amount, the model's, or one typed; the answer wins over both and isn't asked again.
    Another box in doubt is on the bench for reference, asking nothing."""
    from test_check import drop

    from taxbinder import bench, check
    from taxbinder.web.app import AppState

    doc = drop(store, "w2.pdf", _w2())
    reader = modelread.ModelReader(AppState(vault_path=store.vault.root, store=store))
    monkeypatch.setattr(llm, "current", lambda where: llm.catalogue(tmp_path)[0])
    monkeypatch.setattr(reader, "_engine", lambda model: Stand({"2": 15500.0, "7": 50.0}))
    reader.read_now(store, doc)
    kept = {k["box"]: k for k in bench.bench(store, doc)["kept"]}
    assert kept["2"]["key"] and kept["2"]["why"] and not kept["7"]["key"] and kept["7"]["why"]
    [asked] = [f for f in check.report(store).asked if f.doc.id == doc]
    assert asked.question == "boxes" and "1 amount read two ways" in asked.note
    boxes = asked.boxes
    assert [b["box"] for b in boxes] == ["2"]  # box 7 isn't a key box: not asked
    assert boxes[0]["rules"] == "15,000.00" and boxes[0]["model"] == "15,500.00"
    from test_web import PORT, client

    web = client(AppState(vault_path=store.vault.root, store=store, port=PORT, emit=lambda e: None))
    for page in ("/check", "/parts/asks"):  # the question, on Check and on Add documents
        html = web.get(page).text
        assert "Amounts to confirm" in html and 'data-box="2"' in html and "15,500.00" in html
    with pytest.raises(ValueError, match="key boxes"):
        check.confirm_box(store, doc, "7", "50", "t")
    check.confirm_box(store, doc, "2", "15,500", "t")
    assert not [f for f in check.report(store).asked if f.doc.id == doc and f.question == "boxes"]
    kept = {k["box"]: k for k in bench.bench(store, doc)["kept"]}
    assert kept["2"]["why"] == "" and kept["2"]["value"] == "15,500.00"  # the answer, taken


def test_a_file_whose_boxes_cant_be_read_never_breaks_check(store: RecordStore, monkeypatch) -> None:  # noqa: ANN001, F811
    """A PDF where pdfium sees more pages than pdfplumber: the reader stops at the last page it
    can open, and a file that still fails is left out of the amount questions."""
    import pdfplumber
    from test_check import drop

    from taxbinder import check
    from taxbinder.extract import figures, forms

    doc = drop(store, "w2.pdf", _w2())
    real = pdfplumber.open

    class Short:  # the same PDF, its last page missing
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            self.pdf = real(*a, **k)
            self.pages = self.pdf.pages[:0]

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a):  # noqa: ANN002, ANN204
            self.pdf.close()

    monkeypatch.setattr(pdfplumber, "open", Short)
    assert forms.read(store.blobs.get(store.get("document", doc)[0].sha256)) == {}
    monkeypatch.setattr(figures, "read_boxes", lambda *a, **k: 1 / 0)
    assert not [f for f in check.report(store).asked if f.question == "boxes"]


def test_a_consolidated_1099_follows_its_key_boxes_and_the_1099_b_gain() -> None:
    """A consolidated 1099 holds a 1099-DIV, a 1099-INT and a 1099-B; its key
    boxes are dividends, interest, capital gain distributions and the 1099-B's net gain (its
    summary rows added up). An interest payer's row takes one too."""
    from taxbinder.extract import figures
    from taxbinder.ingest.match import same_form

    assert figures.KEY_BOXES["1099_composite"] == ["1a", "1", "2a", "gain"]
    ok = {"status": "confirmed", "grounded": True}
    model = {"forms": {
        "1099_DIV": {"boxes": {"1a": {**ok, "value": 1200.0, "page": 2}}},
        "1099_INT": {"boxes": {"1": {**ok, "value": 80.0, "page": 3}}},
        "1099_B": {"boxes": {
            "A_gain": {**ok, "value": 2000.0, "page": 4},
            "D_gain": {"status": "disagree", "grounded": True, "value": -900.0,
                       "rule_value": -1000.0, "page": 4},
            "D_proceeds": {**ok, "value": 5000.0, "page": 4},
        }},
    }}  # fmt: skip
    got = figures.read_boxes(b"", "1099_composite", ["1a", "1", "gain"], model=model)
    assert got["1a"].value == 1200 and got["1"].value == 80 and got["1"].page == 3
    assert got["gain"].value == 1000 and got["gain"].page == 4  # 2,000 - 1,000 (the rules')
    assert "the rules read -1,000, the AI model -900" in got["gain"].check  # asked about
    assert same_form("1099_int", "1099_composite") and same_form("1099_composite", "1099_div")
    assert not same_form("1099_composite", "1099_int") and not same_form("1099_int", "1099_div")
