"""Desktop backend (§11.4, §12.2): security model and pages, on the test household."""

from __future__ import annotations

import os
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import new_vault
from pdfgen import make_pdf
from quills import ROWS, build

from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState, create_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}


@pytest.fixture
def state(tmp_path: Path):
    vault = new_vault(tmp_path / "vault")
    store = RecordStore(vault)
    build(store, "test")
    reconcile(store, 2025, "test")
    sent: list[dict] = []
    s = AppState(
        vault_path=vault.root,
        store=store,
        port=PORT,
        emit=sent.append,
        today=lambda: date(2026, 9, 29),
    )
    s.sent = sent  # type: ignore[attr-defined]
    yield s
    store.close()


def client(state: AppState, login: bool = True) -> TestClient:
    c = TestClient(create_app(state), base_url=BASE)
    if login:
        r = c.get(f"/bootstrap?t={state.bootstrap_code}", follow_redirects=False)
        assert r.status_code == 200 and 'http-equiv="refresh"' in r.text  # moves on by itself
    return c


def rows_of(c: TestClient, state: AppState, year: int = 2025) -> str:
    """A year's checklist rows as a deck square's panel shows them (the checklist,
    folded into the deck card)."""
    with state.locked_store() as store:
        ids = " ".join(e.id for e in store.list("expectation", year))
    return c.get("/parts/rows", params={"ids": ids}).text


def row_of(html: str, exp_id: str) -> str:
    return html.split(f'data-id="{exp_id}"')[1].split('<div class="xrow"')[0]


# -- security ------------------------------------------------------------------------------


def test_bootstrap_is_one_time_and_cookie_is_strict(state: AppState) -> None:
    c = TestClient(create_app(state), base_url=BASE)
    code = state.bootstrap_code
    r = c.get(f"/bootstrap?t={code}", follow_redirects=False)
    assert r.status_code == 200 and 'http-equiv="refresh" content="0;url=/' in r.text  # no redirect
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert state.bootstrap_code is None
    other = TestClient(create_app(state), base_url=BASE)
    assert other.get(f"/bootstrap?t={code}").status_code == 403


def test_needs_session(state: AppState) -> None:
    c = client(state, login=False)
    assert c.get("/").status_code == 401
    assert c.get("/api/status").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/static/pages.css").status_code == 200  # styles need no session


def test_host_and_origin_checks(state: AppState) -> None:
    c = client(state)
    assert c.get("/", headers={"Host": "evil.example:8765"}).status_code == 421
    assert c.post("/api/capture-page").status_code == 403  # no Origin
    assert (
        c.post("/api/capture-page", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert c.post("/api/capture-page", headers=ORIGIN).status_code == 202


def test_security_headers(state: AppState) -> None:
    r = client(state).get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"


# -- pages ---------------------------------------------------------------------------------


def test_dashboard_answers_the_three_questions(state: AppState) -> None:
    c = client(state)
    html = c.get("/").text  # Results' answers, beside the deck
    side = html.split('class="panel stages" data-side="2025"')[1].split("</section>")[0]
    assert "Extended deadline Oct 15, 2026 · 16 days left" in side
    front = html.split('<section class="panel" data-side="2025"')[1].split("</section>")[0]
    assert "<h3>Check</h3>" in side and ">Not yet<" in front and "to come" in html
    assert "federal payments total $36,000.00" in html  # every finding, under Check
    assert "Friends of the Marsh Wrens $2,000.00" in side
    for page in (html, rows_of(c, state)):
        assert "0000000005" not in page  # account numbers never shown, only hints


def test_a_square_opens_its_checklist_rows(state: AppState) -> None:
    """Each square opens its rows, a row not expected this
    year is listed beside its year, and Expect another document is on every card."""
    import re

    c = client(state)
    html = c.get("/").text
    assert re.search(rf'data-rows="[^"]*{KETTLE}', html)  # the square knows its rows
    assert html.count('class="btn small ghost expect"') == html.count('class="tab"')
    assert 'id="add-row"' in html and 'value="__new__">＋ A new kind of document…' in html
    away = html.split('class="more away"')[1].split("</details>")[0]
    assert "not expected in 2025" in away and 'class="link again"' in away
    rows = rows_of(c, state)
    kettle = row_of(rows, KETTLE)
    assert "Kettle 410" in kettle and "In the vault" in kettle and 'data-act="away"' in kettle
    assert "Not issued" in rows and 'data-act="applicable"' in rows  # not expected: again
    assert ' style="' not in html + rows and "<script" not in rows
    assert client(state, login=False).get(f"/parts/rows?ids={KETTLE}").status_code == 401


def test_documents_and_the_inbox_link_a_portal_download(state: AppState) -> None:
    from taxbinder.web.watcher import process_inbox

    c = client(state)
    assert "No documents yet." in c.get("/documents").text
    pdf = state.store.vault.inbox / "portal" / "Kettle410_2025_K1.pdf"
    pdf.write_bytes(
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    )
    os.utime(pdf, (time.time() - 10, time.time() - 10))
    assert len(process_inbox(state)) == 1  # as the inbox watcher does
    html = c.get("/documents").text
    assert (
        "Kettle410_2025_K1.pdf" in html
        and "fernhollow-properties.kettle-410" in html
        and "portal_window" in html
    )


def test_open_portal_validates_and_emits(state: AppState) -> None:
    c = client(state)
    for bad in (
        "http://portal.example.com",
        "https://user:pw@portal.example.com",
        "",
        "file:///etc",
    ):
        r = c.post("/api/open-portal", json={"url": bad}, headers=ORIGIN)
        assert r.status_code == 400
    ok = c.post("/api/open-portal", json={"url": "https://portal.example.com/x"}, headers=ORIGIN)
    assert ok.status_code == 202
    assert state.sent == [
        {"type": "open_portal", "url": "https://portal.example.com/x", "label": None}
    ]


def test_setup_page_when_no_vault(tmp_path: Path) -> None:
    s = AppState(vault_path=tmp_path / "missing", port=PORT)
    s.open()
    c = client(s)
    r = c.get("/")
    assert r.status_code == 503 and "Welcome to TaxBinder" in r.text and "Get started" in r.text


# -- actions (verify, reconcile, upload) -------------------------------------------------

KETTLE = "2025/fernhollow-properties.kettle-410.k1"
PIKEWAY = "2025/copperfield-collective.pikeway-inv-partners.k1"


def test_no_inline_styles(state: AppState) -> None:
    # CSP style-src 'self' makes WebKit ignore style= attributes; everything goes in style.css.
    c = client(state)
    for path in ("/", "/documents"):
        assert "style=" not in c.get(path).text, path


def test_actions_need_session_and_origin(state: AppState) -> None:
    anon, c = client(state, login=False), client(state)
    for path in (
        "/api/verify",
        "/api/reconcile",
        "/api/upload",
        "/api/year/new",
        "/api/rows",
        "/api/rows/delete",
        "/api/applicability",
        "/api/rematch",
        "/api/rows/edit",
        "/api/payments",
        "/api/payments/delete",
    ):
        assert anon.post(path, json={}, headers=ORIGIN).status_code == 401
        assert c.post(path, json={}).status_code == 403
        assert c.post(path, json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_verify_and_undo_from_the_checklist(state: AppState) -> None:
    c = client(state)
    finding = f"{KETTLE}: not verified"
    assert finding in c.get("/").text
    r = c.post("/api/verify", json={"id": KETTLE}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["verified"] is True
    assert finding not in c.get("/").text
    assert "Undo checked" in row_of(rows_of(c, state), KETTLE)
    r = c.post("/api/verify", json={"id": KETTLE, "verified": False}, headers=ORIGIN)
    assert r.status_code == 200
    assert finding in c.get("/").text
    exp, _ = state.store.get("expectation", KETTLE)
    assert [e.type for e in exp.events][-2:] == ["verified", "unverified"]
    assert c.post("/api/verify", json={"id": "2025/nope.k1"}, headers=ORIGIN).status_code == 404


def test_reconcile_endpoint(state: AppState) -> None:
    r = client(state).post("/api/reconcile", json={"year": 2025}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["counts"]["error"] > 0


def _upload(c: TestClient, data: bytes, name: str, **params: str):
    headers = {**ORIGIN, "X-Filename": name, "Content-Type": "application/octet-stream"}
    return c.post("/api/upload", params=params, content=data, headers=headers)


def test_upload_is_classified_and_matched(state: AppState) -> None:
    c = client(state)
    k1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    r = _upload(c, k1, "Kettle%20410%20K1.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked_to"] == [KETTLE] and body["doc_type"] == "K1" and not body["duplicate"]
    assert body["filename"] == "Kettle 410 K1.pdf"
    assert _upload(c, k1, "again.pdf").json()["duplicate"] is True
    html = c.get("/documents").text
    assert "Kettle 410 K1.pdf" in html and "upload" in html
    assert "Downloaded again 1 time(s)" in html and 'class="again">↻' in html  # one copy kept
    assert list(state.store.vault.inbox.glob("*.pdf")) == []  # nothing left behind


def test_row_upload_links_to_that_row(state: AppState) -> None:
    c = client(state)
    scan = make_pdf(["nothing a classifier can use"])
    r = _upload(c, scan, "scan.pdf", expectation=PIKEWAY, year="2025")
    assert r.status_code == 200, r.text
    assert r.json()["linked_to"] == [PIKEWAY]
    exp, _ = state.store.get("expectation", PIKEWAY)
    assert exp.acquisition == "vaulted" and len(exp.evidence) == 1
    undo = r.json()["undo"]  # stored and attached: one change, so one Undo takes it off
    assert undo["message"].startswith(f"add scan.pdf to {PIKEWAY}")
    assert c.post("/api/undo", json={"sha": undo["sha"]}, headers=ORIGIN).status_code == 200
    exp, _ = state.store.get("expectation", PIKEWAY)
    assert exp.acquisition != "vaulted" and exp.evidence == []
    assert r.json()["doc_id"] not in {d.id for d in state.store.list("document")}
    assert _upload(c, scan, "x.pdf", expectation="2025/nope.k1").status_code == 404
    assert _upload(c, scan, "x.pdf", expectation="not-a-row").status_code == 404


def test_upload_rejects_bad_input(state: AppState, monkeypatch: pytest.MonkeyPatch) -> None:
    import taxbinder.web.app as web

    c = client(state)
    assert _upload(c, b"just some text", "notes.pdf").status_code == 415  # content, not extension
    assert _upload(c, b"", "empty.pdf").status_code == 413
    monkeypatch.setattr(web, "MAX_UPLOAD", 100)
    assert _upload(c, make_pdf(["big"]), "big.pdf").status_code == 413
    monkeypatch.undo()
    r = _upload(c, make_pdf(["x"]), "..%2F..%2Fescape.pdf")
    assert r.status_code == 200 and r.json()["filename"] == "escape.pdf"
    assert not (state.store.vault.root.parent / "escape.pdf").exists()


def test_safe_upload_name() -> None:
    from taxbinder.web.app import safe_upload_name

    assert safe_upload_name("..%2F..%2Fetc%2Fpasswd", "pdf") == "passwd.pdf"
    assert safe_upload_name("C:\\Users\\me\\K1 2025.PDF", "pdf") == "K1 2025.pdf"
    assert safe_upload_name("scan.exe", "png") == "scan.png"  # extension follows the content
    assert safe_upload_name(None, "jpg") == "upload.jpg"
    assert safe_upload_name("a\x00b<c>.pdf", "pdf") == "a_b_c_.pdf"


def test_year_switcher_and_start_next_year(state: AppState) -> None:
    c = client(state)
    html = c.get("/").text
    assert 'id="newyear"' in html and 'data-year="2026" data-from="2025"' in html
    assert html.count('class="ny-keep"') == len(ROWS)  # last year's rows, each with a tick
    r = c.post("/api/year/new", json={"year": 2026, "from": 2025}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["rows"] == len(ROWS)
    html = c.get("/").text  # the new year is now the chosen one, in front
    assert 'data-front="2026"' in html
    assert 'id="newyear"' not in html  # 2027 has no rule parameters yet
    assert 'data-front="2025"' in c.get("/?year=2025").text
    assert 'id="ar-year"' in c.get("/").text and 'data-front="2025"' in c.get("/").text  # sticks
    r = c.post("/api/year/new", json={"year": 2026, "from": 2025}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["rows"] == 0  # nothing twice


def test_start_an_earlier_year(state: AppState) -> None:
    c = client(state)
    html = c.get("/").text
    assert 'id="earlieryear"' in html and 'data-from="2025"' in html
    assert '<option value="2024">' in html and html.count('class="ey-keep"') == len(ROWS)
    assert "tax rules start with 2025" in html  # earlier years aren't checked
    r = c.post("/api/year/new", json={"year": 2024, "from": 2025, "drop": []}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["rows"] == len(ROWS)
    html = c.get("/").text
    assert 'data-front="2024"' in html and 'data-from="2024"' in html  # earlier again: from 2024
    assert 'data-front="2024"' in c.get("/").text
    assert "Across the years" in c.get("/years").text


def test_add_delete_and_applicability_rows(state: AppState) -> None:
    c = client(state)
    row = {
        "year": 2025,
        "institution": "Fidelis",
        "source": "Rollover IRA",
        "doc_type": "1099_r",
        "owner": "primary",
    }
    r = c.post("/api/rows", json=row, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["id"] == "2025/fidelis.rollover-ira.1099-r"
    assert "Rollover IRA" in rows_of(c, state)
    assert c.post("/api/rows", json=row, headers=ORIGIN).status_code == 400  # duplicate
    bad = c.post("/api/rows", json={**row, "doc_type": "nope"}, headers=ORIGIN)
    assert bad.status_code == 400 and "unknown document type" in bad.json()["error"]
    assert c.post("/api/rows", json={**row, "owner": "nobody"}, headers=ORIGIN).status_code == 404

    r = c.post("/api/rows/delete", json={"id": "2025/fidelis.rollover-ira.1099-r"}, headers=ORIGIN)
    assert r.status_code == 200
    assert "Rollover IRA" not in rows_of(c, state)
    assert (
        c.post("/api/rows/delete", json={"id": "2025/nope.k1"}, headers=ORIGIN).status_code == 404
    )

    deferred = "2025/fernhollow-properties.cadence-row.k1"
    r = c.post("/api/applicability", json={"id": deferred, "value": "applicable"}, headers=ORIGIN)
    assert r.status_code == 200
    exp, _ = state.store.get("expectation", deferred)
    assert exp.applicability == "applicable" and exp.applicability_note is None
    r = c.post(
        "/api/applicability", json={"id": deferred, "value": "not_applicable"}, headers=ORIGIN
    )
    exp, _ = state.store.get("expectation", deferred)
    assert exp.applicability == "not_applicable" and "in the app" in exp.applicability_note
    why = {"id": deferred, "value": "not_applicable", "reason": "  account closed "}
    assert c.post("/api/applicability", json=why, headers=ORIGIN).status_code == 200
    exp, _ = state.store.get("expectation", deferred)  # the deck card's reason, kept
    assert exp.applicability_note.startswith("account closed (marked in the app by ")
    r = c.post("/api/applicability", json={"id": deferred, "value": "other"}, headers=ORIGIN)
    assert r.status_code == 400


def test_delete_refused_when_files_are_attached(state: AppState) -> None:
    c = client(state)
    _upload(
        c,
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"]),
        "k1.pdf",
    )
    kettle_row = row_of(rows_of(c, state), KETTLE)
    assert 'data-act="delete"' not in kettle_row  # no Remove button on a row with files
    r = c.post("/api/rows/delete", json={"id": KETTLE}, headers=ORIGIN)
    assert r.status_code == 400 and "attached file" in r.json()["error"]


def test_rematch_button_links_unmatched_uploads(state: AppState) -> None:
    c = client(state)
    k1 = make_pdf(["Schedule K-1 (Form 1065) 2025", "www.irs.gov/Form1065", "Rollover Partners LP"])
    assert _upload(c, k1, "K1 2025_Fidelis_Rollover.pdf").json()["linked_to"] == []
    html = c.get("/documents").text
    assert "1 not matched" in html and 'id="rematch"' in html
    row = {
        "year": 2025,
        "institution": "Fidelis",
        "source": "Rollover",
        "doc_type": "k1_1065",
        "owner": "primary",
    }
    assert c.post("/api/rows", json=row, headers=ORIGIN).status_code == 200
    r = c.post("/api/rematch", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["linked"][0]["linked_to"] == "2025/fidelis.rollover.k1"
    assert 'id="rematch"' not in c.get("/documents").text


def test_checklist_rows_carry_one_status_pill_and_groups(state: AppState) -> None:
    from types import SimpleNamespace as NS

    from taxbinder.web.views import status

    def e(**kw: str) -> NS:
        base = {"applicability": "applicable", "acquisition": "expected", "review": "unreviewed"}
        return NS(**{**base, **kw})

    assert status(e()) == ("to_get", "To come", "warn")
    assert status(e(acquisition="vaulted")) == ("vaulted", "In the vault", "info")
    assert status(e(acquisition="vaulted", review="verified"))[1] == "✓ Checked"
    assert status(e(applicability="deferred_next_year"))[1] == "Next year"
    assert status(e(applicability="not_applicable"))[1] == "Not applicable"

    c = client(state)
    row = row_of(rows_of(c, state), KETTLE)
    assert row.count('class="pill ') == 1 and "In the vault" in row


def test_inline_edit_amount_and_comment(state: AppState) -> None:
    c = client(state)
    row = row_of(rows_of(c, state), KETTLE)
    assert 'data-field="amount"' in row and 'data-field="comment"' in row
    r = c.post(
        "/api/rows/edit",
        json={"id": KETTLE, "amount": "$12,345.6", "comment": "  K-1 final  "},
        headers=ORIGIN,
    )
    assert r.status_code == 200 and {k: v for k, v in r.json().items() if k != "undo"} == {
        "id": KETTLE,
        "amount": "12,345.60",
        "amount_raw": "12345.6",
        "comment": "K-1 final",
    }
    bad = c.post("/api/rows/edit", json={"id": KETTLE, "amount": "lots"}, headers=ORIGIN)
    assert bad.status_code == 400 and "not an amount" in bad.json()["error"]
    row = row_of(rows_of(c, state), KETTLE)
    assert 'value="12345.6"' in row and 'value="K-1 final"' in row
    r = c.post(
        "/api/rows",
        json={
            "year": 2025,
            "institution": "IRS",
            "source": "Q3",
            "doc_type": "payment_confirmation",
            "owner": "primary",
            "amount": "100",
            "comment": "c",
        },
        headers=ORIGIN,
    )
    exp, _ = state.store.get("expectation", r.json()["id"])
    assert exp.amount == 100 and exp.comment == "c"


def test_a_squares_panel_names_its_box_and_another_can_be_picked(state: AppState) -> None:
    c = client(state)
    row = row_of(rows_of(c, state), KETTLE)
    assert "Box 2 <input" in row and "Net rental real estate income" in row
    assert 'class="pick-box"' in row and '<option value="2" selected>' in row
    r = c.post("/api/matrix/boxes", json={"source": "fernhollow-properties.kettle-410",
                                          "doc_type": "k1_1065", "boxes": "1"}, headers=ORIGIN)
    assert r.status_code == 200
    row = row_of(rows_of(c, state), KETTLE)
    assert "Box 1 <input" in row and '<option value="1" selected>' in row  # every year follows it


def test_payments_page_add_upload_delete(state: AppState) -> None:
    c = client(state)
    html = c.get("/payments").text
    assert "Payments · 2025" in html and "Q1 estimate" in html and "Needs a confirmation" in html
    r = c.post(
        "/api/payments",
        json={
            "jurisdiction": "federal",
            "kind": "estimated_q4",
            "date": "2026-01-12",
            "amount": "$5,000",
        },
        headers=ORIGIN,
    )
    assert (
        r.status_code == 200 and r.json()["id"] == "2025/payments/fed.2026-01-12"
    )  # Q4 → prior year
    for bad in (
        {"jurisdiction": "", "kind": "estimated_q1", "date": "2025-04-10", "amount": "1"},
        {"jurisdiction": "CA", "kind": "nope", "date": "2025-04-10", "amount": "1"},
        {"jurisdiction": "CA", "kind": "estimated_q1", "date": "", "amount": "1"},
        {"jurisdiction": "CA", "kind": "estimated_q1", "date": "2025-04-10", "amount": "0"},
    ):
        assert c.post("/api/payments", json=bad, headers=ORIGIN).status_code == 400, bad
    pay = "2025/payments/fed.2026-01-12"
    up = _upload(
        c,
        make_pdf(["EFTPS payment confirmation", "Amount $5,000.00"]),
        "eftps.pdf",
        target=pay,
        year="2025",
    )
    assert up.status_code == 200 and pay in up.json()["linked_to"]
    page = c.get("/payments?year=2025").text.split(f'data-id="{pay}"')[1].split("</tr>")[0]
    assert "Upload confirmation" not in page and "Delete (files stay in the vault)" in page
    edited = c.post(
        "/api/payments/edit",
        json={"id": pay, "date": "2026-01-13", "amount": "5,100"},
        headers=ORIGIN,
    )
    assert edited.status_code == 200 and edited.json()["id"] == pay  # a tax payment keeps its year
    assert state.store.get("claim", pay)[0].amount == 5100
    bad = c.post("/api/payments/edit", json={"id": pay, "amount": "0"}, headers=ORIGIN)
    assert bad.status_code == 400 and "more than zero" in bad.json()["error"]
    gone = c.post("/api/payments/delete", json={"id": pay}, headers=ORIGIN)
    assert gone.status_code == 200 and up.json()["doc_id"] in {
        d.id for d in state.store.list("document")
    }  # the confirmation stays in the vault
    c.post(
        "/api/payments",
        json={"jurisdiction": "CA", "kind": "estimated_q1", "date": "2025-04-01", "amount": "9"},
        headers=ORIGIN,
    )
    gone = c.post(
        "/api/payments/delete", json={"id": "2025/payments/ca.2025-04-01"}, headers=ORIGIN
    )
    assert gone.status_code == 200 and "ca.2025-04-01" not in c.get("/payments").text
    assert (
        c.post("/api/payments/delete", json={"id": KETTLE}, headers=ORIGIN).status_code == 400
    )  # not a payment id (invalid id → 400)


def test_undo_from_history_and_from_the_reply(state: AppState) -> None:
    c = client(state)
    r = c.post("/api/rows/edit", json={"id": KETTLE, "comment": "first"}, headers=ORIGIN)
    undo = r.json()["undo"]
    assert undo["message"].startswith(f"row edit {KETTLE}") and len(undo["sha"]) == 40
    c.post("/api/rows/edit", json={"id": KETTLE, "amount": "5"}, headers=ORIGIN)
    html = c.get("/history").text
    assert "row edit" in html and "act-undo" in html and "reconcile" not in html  # automatic hidden
    assert "reconcile 2025" in c.get("/history?all=1").text
    # the comment edit can't be undone while a later edit of the same row stands
    blocked = c.post("/api/undo", json={"sha": undo["sha"]}, headers=ORIGIN)
    assert blocked.status_code == 400 and "changed again since" in blocked.json()["error"]
    latest = state.store.vault.history.changes(limit=5)
    amount_change = next(
        x for x in latest if x.message.startswith("row edit") and "amount" in x.message
    )
    r = c.post("/api/undo", json={"sha": amount_change.sha}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["undo"]["message"].startswith("undo: row edit")
    exp, _ = state.store.get("expectation", KETTLE)
    assert exp.amount is None and exp.comment == "first"
    assert (
        c.post("/api/undo", json={"sha": undo["sha"]}, headers=ORIGIN).status_code == 200
    )  # now it can
    exp, _ = state.store.get("expectation", KETTLE)
    assert exp.comment == ""
    for sha, why in [
        ("0" * 40, "isn't in the recent history"),
        (state.store.vault.history.changes(limit=500)[-1].sha, "creation"),
    ]:
        r = c.post("/api/undo", json={"sha": sha}, headers=ORIGIN)
        assert r.status_code == 400 and why in r.json()["error"]


def test_undo_a_deleted_row_and_an_added_bill(state: AppState) -> None:
    c = client(state)
    gone = "2025/fernhollow-properties.cirrus.k1"
    r = c.post("/api/rows/delete", json={"id": gone}, headers=ORIGIN)
    assert (
        c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN).status_code
        == 200
    )
    assert state.store.get("expectation", gone)[0].id == gone  # back, from the history
    r = c.post(
        "/api/bills",
        json={
            "kind": "bill",
            "payee": "HOA",
            "installments": [{"due": "2026-11-01", "amount": "300"}],
        },
        headers=ORIGIN,
    )
    [bill] = r.json()["ids"]
    assert (
        c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN).status_code
        == 200
    )
    from taxbinder.store import RecordNotFound

    with pytest.raises(RecordNotFound):
        state.store.get("claim", bill)
    from taxbinder.store import fsck

    assert fsck(state.store.vault) == []


def test_a_receipt_attached_to_a_payment_counts_even_if_unrecognised(state: AppState) -> None:
    c = client(state)
    r = c.post(
        "/api/payments",
        json={"jurisdiction": "CA", "kind": "estimated_q3", "date": "2025-09-12", "amount": "9"},
        headers=ORIGIN,
    )
    pay = r.json()["id"]
    receipt = make_pdf(
        ["Your bank", "Transfer to STATE OF CA", "Sep 12 2025"]
    )  # nothing to recognise
    up = _upload(c, receipt, "bank-transfer.pdf", target=pay, year="2025")
    doc, _ = state.store.get("document", up.json()["doc_id"])
    assert (
        doc.classification.doc_type == "payment_confirmation"
        and "human" in doc.classification.method
    )
    row = c.get("/payments").text.split(f'data-id="{pay}"')[1].split("</tr>")[0]
    assert "Confirmation in the vault" in row


def test_recheck_fixes_receipts_attached_before(state: AppState) -> None:
    """Files people attached earlier, whose type wasn't recognised, get it on the next check."""
    from datetime import UTC, datetime

    from taxbinder.claims import add_payment
    from taxbinder.models.common import Link

    c = client(state)
    up = _upload(c, make_pdf(["something unreadable"]), "old-receipt.pdf").json()["doc_id"]
    with state.lock:
        store = state.store
        pay = add_payment(store, "CA", Decimal("9"), date(2025, 9, 12), "estimated_q3", "t")
        claim, rev = store.get("claim", pay)
        link = Link(doc=up, role="primary", linked_at=datetime.now(UTC), linked_by="avery")
        with store.operation("t", "old link, as before the fix"):
            store.put(claim.model_copy(update={"evidence": [link]}), rev)
    assert c.post("/api/reconcile", json={"year": 2025}, headers=ORIGIN).status_code == 200
    with state.lock:
        assert state.store.get("document", up)[0].classification.doc_type == "payment_confirmation"
        req = next(
            r
            for r in state.store.get("claim", pay)[0].requirements
            if r.id == "payment_confirmation"
        )
        assert req.satisfied_by == up


def test_a_negative_amount_on_a_row(state: AppState) -> None:
    c = client(state)
    exp = state.store.list("expectation", 2025)[0]
    c.post("/api/rows/edit", json={"id": exp.id, "amount": "-12,345"}, headers=ORIGIN)
    assert 'value="-12345"' in row_of(rows_of(c, state), exp.id)


def test_folder_upload_and_print(state: AppState) -> None:
    import re

    from taxbinder.web.app import UPLOAD_EXTS, UPLOAD_TYPES

    c = client(state)
    html = c.get("/add").text
    assert 'id="fx-folder"' in html and "webkitdirectory" in html
    exts = re.search(r'data-exts="([^"]+)"', html).group(1).split(",")
    accept = re.search(r'id="fx-files"[^>]*accept="([^"]+)"', html).group(1).split(",")
    assert exts == UPLOAD_EXTS and set(UPLOAD_TYPES.values()) <= set(exts)
    assert sorted("." + e for e in exts) == sorted(accept)  # a folder takes what Upload takes
    assert 'data-app="false"' in html
    assert c.post("/api/print", headers=ORIGIN).status_code == 400  # a browser prints itself
    state.in_app = True
    assert 'data-app="true"' in c.get("/backup").text and "act-print" in c.get("/backup").text
    assert c.post("/api/print", headers=ORIGIN).status_code == 202
    assert state.sent[-1] == {"type": "print"}
