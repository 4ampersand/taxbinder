"""Redaction for LLM use: known values and patterns hidden, same placeholder everywhere, and
nothing produced when something would leak. Every value here is fictional."""

from __future__ import annotations

import re

from pdfgen import make_pdf
from test_web import state  # noqa: F401 - fixture
from test_years import store  # noqa: F401 - fixture

from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Taxpayer
from taxbinder.models.registry import Identifier
from taxbinder.privacy import add_value
from taxbinder.redact import bundle, unredact
from taxbinder.redact.detect import known_finders, pattern_finders
from taxbinder.redact.markdown import Leak, check
from taxbinder.redact.tokens import Pseudonyms
from taxbinder.store import RecordStore

SSN, EIN, ACCT, OLD_ACCT = "900-22-4321", "00-8412345", "5801-1144-77", "4410-0099-12"

K1_2025 = [
    "Schedule K-1 (Form 1065) 2025",
    "A Partnership's employer identification number 00-8412345",
    "B Harborview Opportunity Fund II, LP",
    "200 Pine Street, Suite 400, San Francisco, CA 94104",
    "E Partner's identifying number XXX-XX-4321",
    "F Jonathan Q. Example and Maria Example JTWROS",
    "1234 Elm Ct., Cedarville, CA 95014",
    "Account number 5801-1144-77",
    "State: California, New York, Illinois",
    "Box 1 Ordinary business income (loss) 12,345",
    "Questions: ir@harborview.example or 415-555-0134",
]
K1_2024 = [
    "Schedule K-1 (Form 1065) 2024",
    "Partner: EXAMPLE, JONATHAN   SSN 900224321",
    "Account ending in 9912",  # the old account's last 4
    "Old account 4410 0099 12",
]


def _setup(store: RecordStore) -> None:  # noqa: F811
    with store.operation("t", "fixture"):
        store.put(Taxpayer(id="jonathan", name="Jonathan Example", tin=SSN), None)
        src = store.list("source")[0]
        store.put(
            src.model_copy(
                update={
                    "identifiers": [
                        Identifier(number=ACCT, kind="primary"),
                        Identifier(number=OLD_ACCT, kind="legacy"),
                    ]
                }
            ),
            store.get("source", src.id)[1],
        )
    add_value(store, "name", "Maria Example", "spouse", "t")
    add_value(store, "address", "1234 Elm Court, Cedarville, CA 95014", "home", "t")


def _ingest(store: RecordStore, name: str, *pages: list[str]) -> str:  # noqa: F811
    path = store.vault.inbox / name
    path.write_bytes(make_pdf(*pages))
    return ingest_file(store, path, "upload", "t").doc_id


def test_k1_is_redacted_and_placeholders_hold_across_documents(store: RecordStore) -> None:  # noqa: F811
    _setup(store)
    a = _ingest(store, "k1-2025.pdf", K1_2025)
    b = _ingest(store, "k1-2024.pdf", K1_2024)
    out = bundle(store, [a, b], "t")
    md = out.markdown
    assert out.documents == 2 and not out.refused
    for secret in (
        "536-22",
        "4321",
        "1234567",
        "1144",
        "0099",
        "9912",
        "Jonathan",
        "Maria",
        "Elm",
        "95014",
        "94104",
        "ir@harborview",
        "555-0134",
        "200 Pine",
    ):
        assert secret not in md, secret
    # kept: what the analysis needs
    for kept in (
        "Harborview Opportunity Fund II",
        "California",
        "Illinois",
        "12,345",
        "Schedule K-1",
        "San Francisco",
        "**— Page 1 —**",
        "Document 2",
    ):
        assert kept in md, kept
    tokens = Pseudonyms(store).display()
    tin = next(t for t, v in tokens.items() if v == SSN)
    acct = next(t for t, v in tokens.items() if v == ACCT)
    assert md.count(f"[{tin}]") >= 2  # 2025 masked + 2024 bare digits: the same placeholder
    assert md.count(f"[{acct}]") >= 3  # new number, old number, "ending in" the old one
    assert "JTWROS" in md  # the name is hidden, the account title kept
    assert "Partner: [PERSON_01] SSN [TIN_01]" in md  # labels around a reversed name stay


def test_tokens_are_stable_and_saved_encrypted(store: RecordStore) -> None:  # noqa: F811
    _setup(store)
    a = _ingest(store, "k1-2025.pdf", K1_2025)
    first = bundle(store, [a], "t").markdown
    again = bundle(store, [a], "t").markdown
    assert first == again
    on_disk = (store.vault.records / "registry" / "pseudonyms.yaml").read_text()
    assert "900-22-4321" not in on_disk and "Maria Example" not in on_disk
    reply = "The partner [TIN_01] holds ACCT_01; see [PERSON_02]."
    back = unredact(reply, store)
    assert SSN in back or "Jonathan" in back


def test_leak_check_refuses(store: RecordStore) -> None:  # noqa: F811
    _setup(store)
    tokens = Pseudonyms(store)
    finders = known_finders(store, tokens) + pattern_finders()
    try:
        check("Totals for 900 22 4321", finders)
        raise AssertionError("should refuse")
    except Leak as e:
        assert "4321" not in str(e)  # the message never repeats the value


def test_not_pdf_or_no_text_is_refused(store: RecordStore) -> None:  # noqa: F811
    blank = _ingest(store, "empty.pdf", [])
    out = bundle(store, [blank], "t")
    assert out.documents == 0 and out.refused and "read from a scan or photo" in out.refused[0][1]


def test_app_prepare_for_chat_and_privacy_page(state) -> None:  # noqa: ANN001, F811
    from test_web import ORIGIN, _upload, client

    c = client(state)
    assert (
        c.post(
            "/api/privacy",
            json={"kind": "name", "value": "Maria Example", "note": "spouse"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    page = c.get("/privacy").text
    assert "M… E…" in page and "Maria" not in page and "spouse" in page
    doc = _upload(
        c, make_pdf(["Thank you, Maria Example", "SSN 900-22-4321"]), "letter.pdf"
    ).json()["doc_id"]
    r = c.post("/api/redact", json={"ids": [doc]}, headers=ORIGIN)
    j = r.json()
    assert r.status_code == 200 and j["documents"] == 1 and "undo" not in j
    assert "Maria" not in j["markdown"] and "536-22" not in j["markdown"]
    assert j["hidden"] == {"PERSON": 1, "TIN": 1}
    assert 'id="chat-prep"' in c.get("/documents").text
    who = re.search(r"Thank you, \[(PERSON_\d+)\]", j["markdown"]).group(1)
    back = c.post("/api/unredact", json={"text": f"Ask [{who}]."}, headers=ORIGIN).json()
    assert "Maria Example" in back["text"]
    saved = c.post("/api/redact/save", json={"ids": [doc]}, headers=ORIGIN)
    assert saved.status_code == 200 and (state.downloads_dir / saved.json()["name"]).exists()
    assert c.post("/api/redact", json={"ids": []}, headers=ORIGIN).status_code == 400
    assert c.post("/api/privacy/remove", json={"index": 0}, headers=ORIGIN).status_code == 200


def test_masked_numbers_are_found_quickly_next_to_long_filler(store: RecordStore) -> None:  # noqa: F811
    """A run of X's or asterisks without digits after it once took minutes (backtracking)."""
    import time

    _setup(store)
    tokens = Pseudonyms(store)
    finders = known_finders(store, tokens) + pattern_finders()
    text = ("X" * 400 + " ") * 50 + "\nPartner's identifying number XXX-XX-4321\n" + "*" * 3000
    start = time.monotonic()
    from taxbinder.redact.detect import apply, find

    out = apply(text, find(text, finders, tokens))
    assert time.monotonic() - start < 2
    assert "4321" not in out  # still found and hidden


def test_a_household_name_part_is_hidden_on_its_own(store: RecordStore) -> None:  # noqa: F811
    """'EXAMPLE FAMILY TRUST' or a name split by the layout: the surname alone is hidden too."""
    _setup(store)
    a = _ingest(
        store,
        "trust.pdf",
        [
            "Schedule K-1 (Form 1065) 2025",
            "Partner: EXAMPLE FAMILY TRUST",
            "Contact: Jonathan",
            "at the fund's office",
        ],
    )
    md = bundle(store, [a], "t").markdown
    assert "EXAMPLE" not in md.upper().replace("[", " ") or "[NAME_" in md
    assert "Jonathan" not in md and "Example" not in md and "EXAMPLE" not in md
    assert "FAMILY TRUST" in md  # the words that aren't names stay
