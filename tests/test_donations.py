"""Donations page: add gifts, see which letters and forms each needs, attach them."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pdfgen import make_pdf
from test_web import (  # noqa: F401 - `state` is a fixture
    ORIGIN,
    _upload,
    client,
    row_of,
    rows_of,
    state,
)
from test_years import store  # noqa: F401 - fixture

from taxbinder.donations import add_donation, donation_rows, edit_donation
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState


def test_add_donation_and_paperwork(store: RecordStore) -> None:  # noqa: F811
    publicradio = add_donation(store, "PUBLICRADIO", Decimal("250"), date(2025, 9, 20), "cash", "t")
    small = add_donation(store, "Food Bank", Decimal("100"), date(2025, 9, 21), "cash", "t")
    stock = add_donation(
        store,
        "Stanford",
        Decimal("12000"),
        date(2025, 12, 1),
        "securities",
        "t",
        security="AAPL",
        shares=Decimal("50"),
        cost_basis_per_share=Decimal("40"),
        acquired=date(2020, 1, 2),
    )
    reconcile(store, 2025, "t")
    rows = {r.c.id: r for r in donation_rows(store, 2025)}
    assert rows[publicradio].letter[1] == "Needed" and rows[publicradio].records[1] == "Not needed"
    assert rows[publicradio].paperwork == ("Letter needed", "warn")
    assert rows[small].paperwork == ("Not needed", "dim") and not rows[small].needs_paperwork
    assert rows[stock].c.donation.holding_period == "long_term"
    assert rows[stock].records[1] == "Needed" and rows[stock].noncash
    assert rows[stock].paperwork == ("Transfer record needed", "warn")
    assert rows[stock].kind == "Stock or other securities"

    moved = edit_donation(store, publicradio, "t", date=date(2026, 1, 5), amount=Decimal("300"))
    assert (
        moved == "2026/donations/2025-09-20.publicradio"
        and store.get("claim", moved)[0].year == 2026
    )
    assert (
        edit_donation(store, moved, "t", payee="PUBLICRADIO") == moved
    )  # nothing changed: no commit
    late = edit_donation(store, stock, "t", acquired=date(2025, 6, 1))
    assert store.get("claim", late)[0].donation.holding_period == "short_term"


def test_attached_as_a_transfer_record_counts_as_one(state: AppState) -> None:  # noqa: F811
    """A broker's or bank's transfer statement read as something else, then
    attached with "Attach transfer record", is the transfer record the gift needs."""
    from taxbinder.review import link_document, unlink_document

    c = client(state)
    gift = c.post("/api/donations", json={"payee": "Wren Fund", "date": "2025-08-15",
                  "amount": "9000", "kind": "securities"}, headers=ORIGIN).json()["id"]  # fmt: skip
    doc = _upload(c, make_pdf(["Wren Fund", "Transfer of 40 shares"]), "transfer.pdf",
                  target=gift, year="2025", as_type="donation_ack").json()["doc_id"]  # fmt: skip
    gift_row = lambda: next(r for r in donation_rows(state.store, 2025) if r.c.id == gift)  # noqa: E731
    reconcile(state.store, 2025, "t")
    assert gift_row().paperwork[0].startswith("Transfer record needed")
    unlink_document(state.store, doc, gift, "t")
    link_document(state.store, doc, gift, "t", as_type="form_8283_support")
    assert state.store.get("document", doc)[0].classification.doc_type == "form_8283_support"
    reconcile(state.store, 2025, "t")
    assert gift_row().records[0] == "have"


def test_a_transfer_record_read_as_an_earlier_year_is_for_the_gift(state: AppState) -> None:  # noqa: F811
    """A statement whose dates are short (10/15/25) and that names an older year (an account
    opened in 2020) still counts for the year of the gift."""
    c = client(state)
    gift = c.post("/api/donations", json={"payee": "Wren Fund", "date": "2025-08-15",
                  "amount": "9000", "kind": "securities"}, headers=ORIGIN).json()["id"]  # fmt: skip
    pdf = make_pdf(["Wren Fund", "Member since 2020", "Shares received 08/15/25", "2020"])
    doc = _upload(c, pdf, "transfer.pdf", target=gift, year="2025",
                  as_type="form_8283_support").json()["doc_id"]  # fmt: skip
    assert state.store.get("document", doc)[0].classification.tax_year == 2025
    reconcile(state.store, 2025, "t")
    row = next(r for r in donation_rows(state.store, 2025) if r.c.id == gift)
    assert row.records[0] == "have"


def test_donations_page(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/donations").text
    assert (
        "Donations · 2025" in html
        and 'id="add-gift-open"' in html
        and "Friends of the Marsh Wrens" in html
    )
    r = c.post(
        "/api/donations",
        json={"payee": "PUBLICRADIO", "date": "2025-09-20", "amount": "$250", "kind": "cash"},
        headers=ORIGIN,
    )
    assert r.status_code == 200, r.text
    gift = r.json()["id"]
    row = c.get("/donations").text.split(f'data-id="{gift}"')[1].split("</tr>")[0]
    assert (
        "PUBLICRADIO" in row
        and 'pill warn">Letter needed' in row
        and "Upload letter</button>" in row
    )
    letter = make_pdf(
        ["PUBLICRADIO", "Member services", "September 2025"]
    )  # nothing the recogniser knows
    up = _upload(
        c, letter, "publicradio-letter.pdf", target=gift, year="2025", as_type="donation_ack"
    )
    assert gift in up.json()["linked_to"]
    row = c.get("/donations").text.split(f'data-id="{gift}"')[1].split("</tr>")[0]
    assert 'pill ok">Complete' in row and 'class="small act-upload"' not in row  # nothing needed
    assert "Delete (files stay in the vault)" in row and "act-gift-edit" in row
    visible = re.sub(r"<[^>]*>", " ", c.get("/donations").text)  # text, not attributes
    assert "8283" not in visible  # the forms are the preparer's business, not the user's
    r = c.post(
        "/api/donations/edit",
        json={"id": gift, "amount": "300", "date": "2025-09-22"},
        headers=ORIGIN,
    )
    assert r.status_code == 200 and state.store.get("claim", gift)[0].amount == 300
    other = make_pdf(["PUBLICRADIO", "Pledge form"])
    up2 = _upload(c, other, "pledge.pdf", target=gift, year="2025", as_type="none")
    assert gift in up2.json()["linked_to"]
    links = {link.doc: link.role for link in state.store.get("claim", gift)[0].evidence}
    assert links[up2.json()["doc_id"]] == "other"
    for bad, why in [
        ({"payee": "", "date": "2025-09-20", "amount": "1"}, "which charity"),
        ({"payee": "X", "date": "", "amount": "1"}, "date of the gift"),
        ({"payee": "X", "date": "2025-09-20", "amount": "0"}, "more than zero"),
        ({"payee": "X", "date": "2025-09-20", "amount": "5", "kind": "nope"}, "unknown kind"),
    ]:
        r = c.post("/api/donations", json=bad, headers=ORIGIN)
        assert r.status_code == 400 and why in r.json()["error"], bad
    r = c.post(
        "/api/donations",
        json={"payee": "Typo", "date": "2025-09-21", "amount": "5"},
        headers=ORIGIN,
    )
    typo = r.json()["id"]
    deleted = c.post("/api/donations/delete", json={"id": typo}, headers=ORIGIN)
    assert deleted.status_code == 200
    assert (
        c.post("/api/donations/delete", json={"id": gift}, headers=ORIGIN).status_code == 200
    )  # its letter stays in the vault
    assert (
        "to do</span>" in c.get("/donations").text
    )  # the binder's Donations tab: paperwork still missing for the household's gifts
    blocked = c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN)
    assert blocked.status_code == 400  # adding it can't be undone: it was deleted since
    undo_delete = c.post("/api/undo", json={"sha": deleted.json()["undo"]["sha"]}, headers=ORIGIN)
    assert undo_delete.status_code == 200
    assert state.store.get("claim", typo)[0].payee == "Typo"  # the delete undone
    for path in ("/api/donations", "/api/donations/edit", "/api/donations/delete"):
        assert client(state, login=False).post(path, json={}, headers=ORIGIN).status_code == 401


def test_documents_page_follows_the_year(state: AppState) -> None:  # noqa: F811
    c = client(state)
    r = c.post(
        "/api/donations",
        json={"payee": "PUBLICRADIO", "date": "2026-01-26", "amount": "100", "kind": "cash"},
        headers=ORIGIN,
    )
    gift = r.json()["id"]
    up = _upload(
        c, make_pdf(["PUBLICRADIO", "Thank you"]), "publicradio-2026.pdf", target=gift, year="2026"
    )
    loose = _upload(c, make_pdf(["Something", "unreadable"]), "mystery.pdf")  # no year, no match
    assert up.status_code == 200 and loose.status_code == 200
    y2025 = c.get("/documents?year=2025").text
    y2026 = c.get("/documents?year=2026").text
    assert "publicradio-2026.pdf" in y2026 and "publicradio-2026.pdf" not in y2025
    assert "mystery.pdf" in y2025 and "mystery.pdf" in y2026  # year unknown: shown in every year
    assert "Documents · 2026" in y2026
    # the year menu: any year, with its count, or all of them
    assert '<option value="2026" selected>2026 (2)</option>' in y2026
    assert '<option value="2025">2025 (' in y2026 and '<option value="all">All years (' in y2026
    every = c.get("/documents?all=1").text
    assert "publicradio-2026.pdf" in every and '<option value="all" selected>' in every


def test_edit_and_delete_documents(state: AppState) -> None:  # noqa: F811
    c = client(state)
    gift = c.post(
        "/api/donations",
        json={"payee": "PUBLICRADIO", "date": "2025-09-20", "amount": "300", "kind": "cash"},
        headers=ORIGIN,
    ).json()["id"]
    up = _upload(
        c,
        make_pdf(["PUBLICRADIO", "Thanks"]),
        "publicradio.pdf",
        target=gift,
        year="2025",
        as_type="donation_ack",
    )
    doc = up.json()["doc_id"]
    assert state.store.get("claim", gift)[0].requirements[0].satisfied_by == doc
    row = c.get("/documents?year=2025").text.split(f'data-id="{doc}"')[1].split("</tr>")[0]
    assert "act-doc-edit" in row and "act-doc-delete" in row

    r = c.post(
        "/api/documents/edit",
        json={"id": doc, "doc_type": "donation_ack", "tax_year": "2026"},
        headers=ORIGIN,
    )
    assert r.status_code == 200
    cl = state.store.get("document", doc)[0].classification
    assert cl.tax_year == 2026 and "human" in cl.method
    bad = c.post(
        "/api/documents/edit", json={"id": doc, "doc_type": "nope", "tax_year": ""}, headers=ORIGIN
    )
    assert bad.status_code == 400 and "unknown document type" in bad.json()["error"]

    r = c.post("/api/documents/edit", json={"id": doc, "detach": [gift]}, headers=ORIGIN)
    claim = state.store.get("claim", gift)[0]
    assert (
        r.status_code == 200 and claim.evidence == [] and claim.requirements[0].satisfied_by is None
    )
    r = c.post("/api/documents/edit", json={"id": doc, "attach": gift}, headers=ORIGIN)
    assert (
        r.status_code == 200
        and state.store.get("claim", gift)[0].requirements[0].satisfied_by == doc
    )

    # who sent it, the account and whose it is, with the rest, as one change
    people = sorted(t.id for t in state.store.list("taxpayer"))
    assert 'id="ed-issuer"' in c.get("/documents?year=2025").text
    before = state.store.get("document", doc)[0]
    r = c.post("/api/documents/edit", json={"id": doc, "issuer": "Public Radio Friends",
               "account": "00-1234-5678", "taxpayers": people[:1]}, headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200
    now = state.store.get("document", doc)[0]
    assert state.store.get("institution", now.classification.issuer)[0].name == (
        "Public Radio Friends")
    assert now.classification.account_number == "00-1234-5678"
    assert now.recipient.taxpayers == people[:1]
    row = c.get("/documents?all=1").text.split(f'data-id="{doc}"')[1].split(">")[0]
    assert 'data-acct="5678"' in row and "1234-5678" not in row  # the last 4 only
    undone = c.post("/api/undo", json={"sha": r.json()["undo"]["sha"]}, headers=ORIGIN)
    back = state.store.get("document", doc)[0]
    assert undone.status_code == 200 and back.classification.issuer == before.classification.issuer
    assert back.recipient == before.recipient and back.classification.account_number is None

    gone = c.post("/api/documents/delete", json={"id": doc}, headers=ORIGIN)
    assert gone.status_code == 200
    assert doc not in {d.id for d in state.store.list("document")}
    assert state.store.get("claim", gift)[0].evidence == []
    undone = c.post("/api/undo", json={"sha": gone.json()["undo"]["sha"]}, headers=ORIGIN)
    assert undone.status_code == 200 and state.store.get("claim", gift)[0].evidence[0].doc == doc
    assert c.get(f"/documents/{doc}/file").status_code == 200  # the file itself came back
    # several at once: one change, one Undo
    two = [_upload(c, make_pdf(["PUBLICRADIO", f"Thanks {n}"]), f"thanks-{n}.pdf", target=gift,
                   year="2025", as_type="donation_ack").json()["doc_id"] for n in (1, 2)]  # fmt: skip
    assert 'id="del-selected"' in c.get("/documents?year=2025").text
    gone = c.post("/api/documents/delete", json={"ids": two}, headers=ORIGIN)
    assert gone.status_code == 200 and gone.json()["deleted"] == two
    assert not set(two) & {d.id for d in state.store.list("document")}
    c.post("/api/undo", json={"sha": gone.json()["undo"]["sha"]}, headers=ORIGIN)
    assert set(two) <= {d.id for d in state.store.list("document")}
    for path in ("/api/documents/edit", "/api/documents/delete"):
        assert client(state, login=False).post(path, json={}, headers=ORIGIN).status_code == 401


def test_checklist_portal_url(state: AppState) -> None:  # noqa: F811
    c = client(state)
    exp = next(e for e in state.store.list("expectation", 2025))
    source = state.store.get("source", exp.source)[0]
    siblings = [
        e.id
        for e in state.store.list("expectation")
        if state.store.get("source", e.source)[0].institution == source.institution
    ]
    r = c.post(
        "/api/rows/edit", json={"id": exp.id, "portal": "us.tidemark.example/home"}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text
    inst = state.store.get("institution", source.institution)[0]
    assert inst.portal.url == "https://us.tidemark.example/home"  # https:// added
    html = rows_of(c, state)
    for sid in siblings:  # every row of that institution shows it
        row = row_of(html, sid)
        assert (
            'data-url="https://us.tidemark.example/home"' in row and "us.tidemark.example ↗" in row
        )
    for bad in ("http://x.com", "https://me:pw@x.com", "not a url"):
        r = c.post("/api/rows/edit", json={"id": exp.id, "portal": bad}, headers=ORIGIN)
        assert r.status_code == 400, bad
    assert (
        c.post("/api/rows/edit", json={"id": exp.id, "portal": ""}, headers=ORIGIN).status_code
        == 200
    )
    assert state.store.get("institution", source.institution)[0].portal.url is None
    assert 'data-act="portal"' not in rows_of(c, state)
