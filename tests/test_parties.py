"""Who sent a form and which account, read by their place on the form."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pymupdf
import pytest
from helpers import new_vault
from kformgen import k1
from samples.generate import generate
from samples.score import score

from taxbinder.extract.parties import clean_name, read_parties
from taxbinder.ingest.inbox import ingest_file
from taxbinder.institutions import find, name_key, remember
from taxbinder.models import Institution
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
CAPTION = "PAYER'S name, street address, city or town, state or province, country, ZIP"


def form(
    issuer: str,
    account: str = "",
    ein: str = "00-0000321",
    letterhead: str = "",
    recipient: str = "Avery Quill",
    ssn: str = "900-00-0101",
    corrected: bool = False,
) -> bytes:
    """A 1099-INT laid out like the IRS form: captions in boxes, values under them."""
    doc = pymupdf.open()
    page = doc.new_page()
    put = lambda x, y, t, s=9: page.insert_text((x, y), t, fontsize=s)  # noqa: E731
    if letterhead:
        put(36, 40, letterhead, 14)
    if corrected:
        put(330, 30, "CORRECTED", 9)
    put(330, 46, "Form 1099-INT  2025", 12)
    put(330, 60, "Interest Income", 7)
    put(39, 88, CAPTION, 5.5)
    lines = [issuer, "12 Commerce Way", "Albany Springs, NY 12900"] if issuer else []
    for i, line in enumerate(lines):
        put(39, 100 + 11 * i, line)
    put(39, 138, "PAYER'S TIN", 5.5)
    put(39, 150, ein)
    put(181, 138, "RECIPIENT'S TIN", 5.5)
    put(181, 150, ssn)
    put(39, 168, "RECIPIENT'S name", 5.5)
    put(39, 180, recipient)
    if account:
        put(39, 212, "Account number (see instructions)", 5.5)
        put(39, 224, account)
    put(333, 108, "1 Interest income", 7)
    put(410, 124, "1,766.29", 10)
    data = doc.tobytes()
    doc.close()
    return data


def test_issuer_ein_and_account_by_their_captions() -> None:
    p = read_parties(form("Tamarack Savings Bank", "88120-3304"), "1099_int")
    assert (p.issuer, p.issuer_tin) == ("Tamarack Savings Bank", "00-0000321")
    assert p.account == "88120-3304"
    capitals = read_parties(form("TAMARACK SAVINGS BANK, N.A."), "1099_int")
    assert capitals.issuer == "Tamarack Savings Bank, N.A."
    letterhead = read_parties(form("", letterhead="LARKMOOR SECURITIES LLC"), "1099_composite")
    assert letterhead.issuer == "Larkmoor Securities LLC"  # no name under the caption
    ssa = read_parties(form(""), "ssa_1099")
    assert ssa.issuer == "Social Security Administration"


def test_an_address_or_a_heading_is_never_the_name() -> None:
    assert read_parties(form("", letterhead=""), "1099_int").issuer is None
    # a K-1 whose headings ("Partner's Share of Income", a caption) are as large as its text
    assert read_parties(k1(), "k1_1065").issuer is None


def test_clean_name() -> None:
    assert clean_name("  GANNET HOME LENDING ,") == "Gannet Home Lending"
    assert clean_name("TOWN OF HOLLIS FERRY") == "Town of Hollis Ferry"
    assert clean_name("WILLOWBEND HSA BANK") == "Willowbend HSA Bank"
    assert clean_name("Kestrel Media Group") == "Kestrel Media Group"  # as printed


def test_one_institution_however_spelled() -> None:
    assert name_key("The Larkmoor Securities, L.L.C.") == name_key("Larkmoor Securities LLC")
    known = [Institution(id="larkmoor", name="Larkmoor Securities LLC", tin="00-0000104")]
    assert find("LARKMOOR SECURITIES, INC.", None, known) == "larkmoor"
    assert find("Larkmoor Sec.", "00-0000104", known) == "larkmoor"  # the EIN decides
    assert find("Larkmoor Securities", "00-0000999", known) is None  # another EIN: another issuer
    assert find("Gannet Home Lending", "00-0000104", known) is None  # a misread EIN merges nothing
    banks = [Institution(id="tamarack", name="Tamarack Savings Bank", tin="00-0000103")]
    assert find("Willowbend HSA Bank", "00-0000103", banks) is None  # "Bank" isn't enough
    found, record = remember("Larkmoor Securities, Inc.", None, "1099_div", known)
    assert found == "larkmoor" and record.aliases == ["Larkmoor Securities, Inc."]
    new, record = remember("Tamarack Savings Bank", "00-0000103", "1099_int", known)
    assert (new, record.kind, record.tin) == ("tamarack-savings-bank", "bank", "00-0000103")


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    yield s
    s.close()


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


def test_an_empty_vault_learns_its_issuers(
    store: RecordStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    drop(store, "first.pdf", form("Kestrel Media Group"))
    store.undo(store.completed[-1].sha, "test")  # the document and its new institution go together
    assert store.list("document") == [] and store.list("institution") == []
    first = drop(store, "a.pdf", form("Tamarack Savings Bank", "88120-3304"))
    second = drop(store, "b.pdf", form("TAMARACK SAVINGS BANK INC", "88120-3304"))
    [bank] = store.list("institution")
    assert (bank.name, bank.kind) == ("Tamarack Savings Bank", "bank")
    assert bank.aliases == ["Tamarack Savings Bank Inc"]  # capitals cleaned, spelling kept
    for doc in (first, second):
        assert store.get("document", doc)[0].classification.issuer == bank.id
    on_disk = (store.vault.records / f"registry/institutions/{bank.id}.yaml").read_text()
    assert "ENC[age:" in on_disk and "00-0000321" not in on_disk  # the EIN is encrypted
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("taxbinder"))
    assert "3304" not in ours and "00-0000321" not in ours  # (the app silences pdfminer)


def test_each_document_keeps_its_issuers_ein(store: RecordStore) -> None:
    from taxbinder.ingest.inbox import fill_issuer_tins

    first = drop(store, "a.pdf", form("Tamarack Savings Bank", "88120-3304", ein="00-0000103"))
    second = drop(store, "b.pdf", form("Kestrel Media Group", ein="00-0000207"))
    assert store.get("document", first)[0].classification.issuer_tin == "00-0000103"
    [path] = store.vault.records.rglob(f"*{first}*.yaml")
    assert "ENC[age:" in path.read_text() and "00-0000103" not in path.read_text()

    # a document added before documents kept the EIN gets it by reading its page again
    doc, rev = store.get("document", second)
    with store.operation("test", "two names, one EIN, in one record"):
        c = doc.classification.model_copy(update={"issuer_tin": None})
        store.put(doc.model_copy(update={"classification": c}), rev)
    assert fill_issuer_tins(store, "auto:ingest") == 1
    assert store.get("document", second)[0].classification.issuer_tin == "00-0000207"
    assert fill_issuer_tins(store, "auto:ingest") == 0  # nothing left to do


def test_one_issuer_is_one_ein() -> None:
    """The same EIN is the same issuer whatever the name; a different EIN is another
    issuer even with the same name; no EIN read, the name decides."""
    from taxbinder.extract.crosscheck import tin_printed
    from taxbinder.institutions import model_tin, same_ein

    known = [Institution(id="harbor-point", name="Harbor Point Partners LP", tin="00-0000411")]
    assert find("Harbor Point Income Fund LP", "00-0000411", known) == "harbor-point"
    # a wholly new name with a known EIN stays apart (a misread EIN merges nothing), and is
    # offered as a note instead
    found, record = remember("HPP Income Fund LP", "00-0000411", "k1_1065", known)
    assert found == "hpp-income-fund" and same_ein([*known, record]) == [[known[0], record]]
    assert find("Harbor Point Partners LP", "00-0000522", known) is None  # a second fund
    found, record = remember("Harbor Point Partners LP", "00-0000522", "k1_1065", known)
    assert (found, record.tin) == ("harbor-point-partners", "00-0000522")
    assert find("Harbor Point Partners L.P.", None, known) == "harbor-point"
    # the model's EIN: printed on the page, or one the binder knows
    assert model_tin({"value": "00-0000522", "printed": True}, set()) == "00-0000522"
    assert model_tin({"value": "000000411", "printed": False}, {"00-0000411"}) == "00-0000411"
    assert model_tin({"value": "00-0000999", "printed": False}, {"00-0000411"}) is None
    assert model_tin({"value": "12-34", "printed": True}, set()) is None
    assert tin_printed("00-0000522", "PAYER'S TIN 0O-0000522")  # a scan's look-alike letter
    assert not tin_printed("00-0000522", "PAYER'S TIN 00-0000523")


def _model_says(store: RecordStore, doc_id: str, tin: str, printed: bool) -> None:
    import json

    doc, rev = store.get("document", doc_id)
    parties = {"sender": {"value": None, "printed": False},
               "sender_tin": {"value": tin, "printed": printed}}  # fmt: skip
    blob = store.blobs.put(json.dumps({"model": "tiny", "forms": {}, "parties": parties}).encode())
    with store.operation("auto:model", f"model: read {doc_id}"):
        store.put(doc.model_copy(update={"model_read": blob.sha256}), rev)


def test_the_models_ein_settles_the_issuer_without_asking(store: RecordStore) -> None:
    from taxbinder import check
    from taxbinder.ingest.inbox import use_model_tin

    drop(store, "a.pdf", form("Tamarack Savings Bank", ein="00-0000103"))
    # its EIN not read by the rules: joined by name, then given the EIN the model read
    second = drop(store, "b.pdf", form("Tamarack Savings Bank", ein=""))
    c = store.get("document", second)[0].classification
    assert (c.issuer, c.issuer_tin) == ("tamarack-savings-bank", None)
    _model_says(store, second, "00-0000103", printed=False)  # not in the text, but known
    assert use_model_tin(store, second, "auto:model")
    c = store.get("document", second)[0].classification
    assert (c.issuer, c.issuer_tin) == ("tamarack-savings-bank", "00-0000103")
    assert not use_model_tin(store, second, "auto:model")  # nothing more to do
    # the same name with another EIN on the page: another issuer, no question
    third = drop(store, "c.pdf", form("Tamarack Savings Bank", ein=""))
    _model_says(store, third, "00-0000555", printed=True)
    assert use_model_tin(store, third, "auto:model")
    issuer = store.get("document", third)[0].classification.issuer
    assert issuer != "tamarack-savings-bank"
    assert store.get("institution", issuer)[0].tin == "00-0000555"
    assert not any(f.doc.id == third for f in check.report(store).asked)
    # and two issuers with different EINs are never merged
    with pytest.raises(ValueError, match="different EINs"):
        check.merge(store, issuer, "tamarack-savings-bank", "test")


def test_documents_page_shows_the_last_4_only(store: RecordStore) -> None:
    from test_web import client

    drop(store, "a.pdf", form("Tamarack Savings Bank", "88120-3304"))
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    page = client(state).get("/documents?year=2025").text
    assert "Tamarack Savings Bank · …3304" in page and "88120" not in page


def test_sample_set_on_an_empty_vault(tmp_path: Path) -> None:
    generate(tmp_path)
    text = score(tmp_path)["groups"]["text"]
    assert text["issuer"] / text["documents"] >= 0.95
    assert text["account right"] == text["accounts"]
    for g in score(tmp_path)["groups"].values():
        assert g["whose wrong"] == 0  # no document given to the wrong person or trust
