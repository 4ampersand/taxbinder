"""One check for each thing the app already does and must keep
doing, and the whole season on the invented household, from an empty checklist to a package."""

from __future__ import annotations

import random
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from samples.generate import _render, plan, returns
from test_sharing import LAN_ORIGIN, pair, shared  # noqa: F401 - `shared` is a fixture
from test_web import ORIGIN, _upload, client, state  # noqa: F401 - `state` is a fixture
from test_years import KETTLE

from taxbinder import matrix
from taxbinder.bills import add_bill
from taxbinder.donations import add_donation
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Expectation
from taxbinder.package.build import build_package
from taxbinder.redact.markdown import Leak, check
from taxbinder.review import link_document, set_verified
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordStore
from taxbinder.web.app import LOCAL_ONLY, AppState
from taxbinder.web.views import tiles
from taxbinder.years import add_row

NOW = datetime(2026, 3, 2, tzinfo=UTC)
K1 = ["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"]


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s, "test")
    yield s
    s.close()


def _drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


# -- Checklist status -----------------------------------------------------------------------


def test_receiving_a_document_doesnt_mark_it_checked(store: RecordStore) -> None:
    _drop(store, "k1.pdf", make_pdf(K1))
    row, _ = store.get("expectation", KETTLE)
    assert row.acquisition == "vaulted" and row.review == "unreviewed"


def test_checking_a_document_doesnt_mark_it_sent(store: RecordStore) -> None:
    new = add_row(store, 2025, "Fidelis", "Rollover IRA", "1099_r", "primary", "t")
    set_verified(store, [new], True, "checked against the paper", "t")
    row, _ = store.get("expectation", new)
    assert row.review == "verified" and row.delivery == "not_sent"


def test_not_applicable_needs_a_reason() -> None:
    row = {"id": "2025/a.b.k1", "year": 2025, "source": "a.b", "doc_type": "k1_1065"}
    with pytest.raises(ValueError, match="requires applicability_note"):
        Expectation(**row, applicability="not_applicable")
    Expectation(**row, applicability="not_applicable", applicability_note="account closed")


# -- Payments, donations, bills -------------------------------------------------------------


def test_a_donation_stays_open_until_its_evidence_is_linked(store: RecordStore) -> None:
    gift = add_donation(store, "Heron Trust", Decimal(600), date(2025, 10, 1), "cash", "t")
    reconcile(store, 2025, "t", today=NOW.date())
    assert store.get("claim", gift)[0].status == "evidence_requested"
    letter = _drop(store, "heron.pdf", make_pdf(["Heron Trust", "Thank you for your gift"]))
    link_document(store, letter, gift, "t", as_type="donation_ack")
    reconcile(store, 2025, "t", today=NOW.date())
    assert store.get("claim", gift)[0].status == "evidenced"


def test_a_scheduled_payment_doesnt_count_as_paid(store: RecordStore) -> None:
    before = tiles(store, 2025)["payments_total"]
    add_bill(store, "tax_payment", "", [("", Decimal(900), date(2026, 1, 15))], "t",
             jurisdiction="federal", payment_kind="estimated_q4")  # fmt: skip
    assert tiles(store, 2025)["payments_total"] == before


# -- Package for the preparer ---------------------------------------------------------------


def test_package_is_one_filer_and_one_year(tmp_path: Path) -> None:
    from taxbinder.entities import create_entity, list_entities
    from taxbinder.store import keys_from_env, open_vault

    vault = new_vault(tmp_path / "vault")
    with RecordStore(vault) as s:
        build(s, "test")
        _drop(s, "k1.pdf", make_pdf(K1))
        _drop(s, "old.pdf", make_pdf(["Brackley Wealth", "2024 Form 1099-B"]))
    create_entity(vault.root, keys_from_env(), "Northwind Holdings Inc.", "1120", "t")
    with RecordStore(open_vault(list_entities(vault.root)[1].path, keys_from_env())) as corp:
        _drop(corp, "corp.pdf", make_pdf(["Schedule K-1 (Form 1065) 2025", "Northwind fund"]))
    with RecordStore(open_vault(vault.root, keys_from_env())) as s:
        result = build_package(s, 2025, tmp_path / "pkg", "t", now=NOW)
    names = [p.name for p in (tmp_path / "pkg").rglob("*.pdf") if p.name != "00-summary.pdf"]
    assert sum(result.files.values()) == len(names) == 1  # the K-1; not 2024, not Northwind's


def test_package_lists_open_items_and_unreadable_documents(
    store: RecordStore, tmp_path: Path
) -> None:
    doc = _drop(store, "k1.pdf", make_pdf(K1))
    blob = store.blobs.find(store.get("document", doc)[0].sha256)
    blob.chmod(0o600)
    blob.write_bytes(b"damaged")
    result = build_package(store, 2025, tmp_path / "pkg", "t", now=NOW)
    assert result.unreadable == [doc] and result.open_items > 0
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO((tmp_path / "pkg" / "00-summary.pdf").read_bytes())) as pdf:
        memo = " ".join(page.extract_text() for page in pdf.pages)
    assert f"Could not read 1 document(s): {doc}" in memo


def test_package_never_marks_itself_sent(store: RecordStore, tmp_path: Path) -> None:
    before = {e.id: e.delivery for e in store.list("expectation", 2025)}
    build_package(store, 2025, tmp_path / "pkg", "t", now=NOW)
    assert {e.id: e.delivery for e in store.list("expectation", 2025)} == before


# -- Matrix ---------------------------------------------------------------------------------


def test_matrix_figures_land_in_their_boxes_and_typed_ones_stay(store: RecordStore) -> None:
    from test_matrix import _k1_on_kettle, _row

    exp, _ = store.get("expectation", KETTLE)
    key = f"{exp.source}|{exp.doc_type}"
    if key not in {r.key for r in matrix.rows(store)}:
        matrix.add_row(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_boxes(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "0 / -15000", "t")  # typed
    _k1_on_kettle(store)  # box 2 on the K-1 is -12,345
    matrix.read_pdfs(store, "t")  # flagged, never replaced
    assert _row(store, key).cells[2025].values == [Decimal(0), Decimal(-15000)]


# -- AI-chat redaction ----------------------------------------------------------------------


def test_redaction_with_a_leftover_value_produces_nothing(store: RecordStore) -> None:
    from taxbinder.models import Taxpayer
    from taxbinder.redact.detect import known_finders, pattern_finders
    from taxbinder.redact.tokens import Pseudonyms

    tin = "900-22-4321"  # invented
    with store.operation("t", "a taxpayer with a number"):
        store.put(Taxpayer(id="jonathan", name="Jonathan Example", tin=tin), None)
    finders = known_finders(store, Pseudonyms(store)) + pattern_finders()
    with pytest.raises(Leak) as e:
        check(f"Totals for {tin.replace('-', ' ')}", finders)
    assert tin[-4:] not in str(e.value)


# -- Backups --------------------------------------------------------------------------------


def test_a_backup_covers_every_filers_vault(tmp_path: Path) -> None:
    from taxbinder import backup
    from taxbinder.entities import create_entity
    from taxbinder.store import init_vault, keys_from_env

    vault, recovery = init_vault(tmp_path / "vault", keys_from_env())
    create_entity(vault.root, keys_from_env(), "Northwind Holdings Inc.", "1120", "t")
    made = backup.create(vault, tmp_path / "out")
    manifest = backup.restore(made.path, recovery, tmp_path / "restored")
    assert manifest["vaults"] == ["vault", "entities/northwind-holdings-inc/vault"]


# -- Home sharing ---------------------------------------------------------------------------


def test_sharing_is_off_on_a_fresh_install(state: AppState) -> None:  # noqa: F811
    from test_sharing import remote

    assert remote(state).get("/pair").status_code == 503


def test_an_unpaired_device_is_refused(shared: AppState) -> None:  # noqa: F811
    from test_sharing import remote

    r = remote(shared)
    assert r.get("/", follow_redirects=False).status_code in (303, 401, 403)
    assert r.post("/api/verify", json={"id": KETTLE}, headers=LAN_ORIGIN).status_code in (401, 403)


def test_sharing_and_key_actions_only_on_this_mac(shared: AppState) -> None:  # noqa: F811
    r = pair(shared)
    for path in sorted(LOCAL_ONLY - {"/bootstrap"}):
        refused = r.post(path, json={}, headers=LAN_ORIGIN).status_code == 403
        assert refused or r.get(path).status_code == 403, path


# -- The whole season -----------------------------------------------------------------------


def test_the_whole_season(tmp_path: Path) -> None:
    """A new household, in the app: last year's return, this year's documents, a check, a
    payment, a donation with its letter, Across the years, the checks, a backup and the package."""
    from taxbinder import household as home

    store = RecordStore(new_vault(tmp_path / "vault"))
    app = AppState(vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
                     today=lambda: NOW.date())  # fmt: skip
    app.downloads_dir = tmp_path / "downloads"
    c = client(app)
    assert "Nothing in your binder yet" in c.get("/?year=2025").text
    assert store.list("expectation", 2025) == []  # an empty checklist

    docs = plan(random.Random(7))
    ret = _upload(c, returns(docs)[0][0], "2024%20return.pdf").json()["doc_id"]
    house = home.worked_out(store, 2025)
    assert house.found_in and house.state == "NY"
    body = {"year": 2025, "return_year": 2024, "filing_status": house.filing_status,
            "state": house.state, "people": [asdict(p) for p in house.people]}  # fmt: skip
    assert c.post("/api/household/confirm", json=body, headers=ORIGIN).status_code == 200
    r = c.post("/api/returns/add-listed", json={"id": ret, "year": 2025}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    rows = store.list("expectation", 2025)
    assert rows and all(e.acquisition == "expected" for e in rows)

    for i, d in enumerate(x for x in docs if x.year == 2025 and x.filer == "household"):
        assert _upload(c, _render(d, random.Random(i)), f"doc-{i}.pdf").status_code == 200
    here = [e for e in store.list("expectation", 2025) if e.acquisition == "vaulted"]
    assert here and all(e.review == "unreviewed" for e in here)
    assert c.post("/api/verify", json={"id": here[0].id}, headers=ORIGIN).status_code == 200

    pay = {
        "jurisdiction": "federal",
        "kind": "estimated_q1",
        "date": "2025-04-14",
        "amount": "4000",
    }
    assert c.post("/api/payments", json=pay, headers=ORIGIN).status_code == 200
    gift = c.post("/api/donations", json={"payee": "Heron Trust", "date": "2025-10-01",
                                          "amount": "600", "kind": "cash"},
                  headers=ORIGIN).json()["id"]  # fmt: skip
    letter = make_pdf(["Heron Trust", "Thank you for your gift", "October 2025"])
    up = _upload(c, letter, "heron.pdf", target=gift, year="2025", as_type="donation_ack")
    assert gift in up.json()["linked_to"]

    assert c.post("/api/matrix/add-all", headers=ORIGIN).status_code == 200
    assert matrix.read_pdfs(store, "t")["read"] > 0
    for r in matrix.rows(store):
        for year, cell in r.cells.items():
            if cell.pending:
                matrix.accept(store, r.source.id, r.doc_type, year, "t")
    filled = [r for r in matrix.rows(store) if any(v is not None for v in r.cells[2025].values)]
    assert filled

    r = c.post("/api/reconcile", json={"year": 2025}, headers=ORIGIN)
    assert r.status_code == 200
    assert store.get("claim", gift)[0].status == "evidenced"
    assert c.post("/api/backup", headers=ORIGIN).json()["vaults"] == 1

    # The terminal command's code, same vault.
    result = build_package(store, 2025, tmp_path / "pkg", "season", now=NOW)
    assert sum(result.files.values()) >= len(here) and not result.unreadable
    assert all(e.delivery == "not_sent" for e in store.list("expectation", 2025))
    for path in ("/", "/payments", "/donations", "/years", "/documents"):
        assert c.get(f"{path}?year=2025").status_code == 200, path
    store.close()
