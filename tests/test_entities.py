"""Entities: one vault each, the same keys, per-client selection, rules by return form, CLI."""

from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pytest
from pdfgen import make_pdf
from test_sharing import LAN_ORIGIN, pair, shared  # noqa: F401 - `shared` is a fixture
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder.entities import create_entity, entity_info, list_entities
from taxbinder.report import year_status
from taxbinder.rules.params import load_params
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import MemoryKeys, RecordStore, init_vault, open_vault, restore_key
from taxbinder.web.app import AppState
from taxbinder.years import add_row


def test_entity_vault_shares_the_households_keys(tmp_path: Path) -> None:
    keys = MemoryKeys()
    base = tmp_path / "vault"
    _, recovery = init_vault(base, keys)
    slug = create_entity(base, keys, "Northwind Holdings Inc.", "1120", "t")
    assert slug == "northwind-holdings-inc"
    assert [r.slug for r in list_entities(base)] == ["household", slug]
    corp = list_entities(base)[1].path
    assert corp == tmp_path / "entities" / slug / "vault"
    with RecordStore(open_vault(corp, keys)) as s:
        assert (entity_info(s).name, entity_info(s).form) == ("Northwind Holdings Inc.", "1120")
        owner, _ = s.get("taxpayer", slug)  # so rows can be added straight away
        assert (owner.name, owner.kind) == ("Northwind Holdings Inc.", "entity")
    other_mac = MemoryKeys()
    assert restore_key(corp, other_mac, recovery).crypto is not None  # one recovery key for all
    for bad, why in [
        ("Household", "main vault"),
        ("Northwind Holdings Inc.", "already exists"),
        ("!!", "letters or digits"),
    ]:
        with pytest.raises(ValueError, match=why):
            create_entity(base, keys, bad, "1120", "t")
    with pytest.raises(ValueError, match="unknown return form"):
        create_entity(base, keys, "Trust", "990", "t")


def test_rules_follow_the_return_form(tmp_path: Path) -> None:
    corp_q4 = load_params(2025, "1120").values["R-PAY-03"]["due"]["federal"][-1]
    assert corp_q4 == date(2025, 12, 15)  # corporations: Q4 in December
    assert load_params(2025, "1120-S").values["R-EXP-01"]["deadline"] == date(2026, 3, 16)
    assert load_params(2025, "1041").values["R-EXP-01"]["extended_deadline"] == date(2026, 9, 30)
    with pytest.raises(ValueError, match="Form 990"):
        load_params(2025, "990")

    keys = MemoryKeys()
    base = tmp_path / "vault"
    init_vault(base, keys)
    slug = create_entity(base, keys, "Acme S Corp", "1120-S", "t")
    with RecordStore(open_vault(list_entities(base)[1].path, keys)) as s:
        add_row(s, 2025, "Acme", "Payroll", "w2", "primary" if False else _owner(s), "t")
        reconcile(s, 2025, "t")
        status = year_status(s, 2025, date(2026, 3, 1))
    assert slug and status.deadline == date(2026, 3, 16) and not status.params_reviewed


def _owner(store: RecordStore) -> str:
    """Entity vaults start empty: add_row needs a taxpayer to own the source."""
    from taxbinder.models import Taxpayer

    with store.operation("t", "owner"):
        store.put(Taxpayer(id="acme", name="Acme S Corp", kind="entity"), None)
    return "acme"


def _corp(state: AppState) -> str:  # noqa: F811
    return create_entity(state.vault_path, _keys(), "Northwind Holdings Inc.", "1120", "t")


def _keys():  # noqa: ANN202
    from taxbinder.store import keys_from_env

    return keys_from_env()  # the tests' in-memory keystore (conftest)


def test_switching_entities_in_the_app(state: AppState) -> None:  # noqa: F811
    from taxbinder import settings

    settings.change(state.store, "test", "other_filers", "on")  # off on a fresh vault
    c = client(state)
    html = c.get("/").text
    assert 'data-entity="household"' in html and "Household · Form 1040" in html
    r = c.post(
        "/api/entities", json={"name": "Northwind Holdings Inc.", "form": "1120"}, headers=ORIGIN
    )
    assert r.status_code == 200 and r.json() == {"entity": "northwind-holdings-inc"}
    html = c.get("/").text  # the new entity is selected, and empty
    assert "Northwind Holdings Inc. · Form 1120 · C corporation" in html
    assert "Nothing in your binder yet" in html
    assert (
        '<option value="northwind-holdings-inc">Northwind Holdings Inc.</option>' in html
    )  # owner
    assert (
        c.post(
            "/api/entities",
            json={"name": "Northwind Holdings Inc.", "form": "1120"},
            headers=ORIGIN,
        ).status_code
        == 400
    )
    assert c.post("/api/entity", json={"slug": "nope"}, headers=ORIGIN).status_code == 404
    assert c.post("/api/entity", json={"slug": "household"}, headers=ORIGIN).status_code == 200
    assert "Fernhollow properties" in c.get("/").text  # back to the household's rows


def test_each_client_keeps_its_own_entity_and_data_stays_apart(shared: AppState) -> None:  # noqa: F811
    slug = _corp(shared)
    sam = pair(shared)
    mac = client(shared) if not hasattr(shared, "local_client") else shared.local_client
    assert mac.post("/api/entity", json={"slug": slug}, headers=ORIGIN).status_code == 200
    assert "Northwind Holdings Inc." in mac.get("/").text.split("<h1>")[0].split('id="page"')[1]
    assert "Household · Form 1040" in sam.get("/").text  # her choice is her own
    with shared.lock:
        corp = shared.store_for(slug)
        with corp.operation("t", "owner"):
            from taxbinder.models import Taxpayer

            corp.put(Taxpayer(id="northwind", name="Northwind Holdings Inc.", kind="entity"), None)
    row = {
        "year": 2025,
        "institution": "Chase",
        "source": "Operating account",
        "doc_type": "1099_int",
        "owner": "northwind",
    }
    assert mac.post("/api/rows", json=row, headers=ORIGIN).status_code == 200
    assert "Operating account" in mac.get("/").text
    assert "Operating account" not in sam.get("/").text  # household: not there
    assert (
        sam.post(
            "/api/entities", json={"name": "X", "form": "1120"}, headers=LAN_ORIGIN
        ).status_code
        == 403
    )


def test_portal_downloads_go_to_the_entity_being_worked_on(state: AppState) -> None:  # noqa: F811
    from taxbinder.web.watcher import process_inbox

    slug = _corp(state)
    state.selected["local"] = slug
    pdf = state.vault_path / "inbox" / "portal" / "statement.pdf"  # where portal windows save
    pdf.write_bytes(make_pdf(["Form 1099-INT 2025", "Chase"]))
    os.utime(pdf, (time.time() - 10, time.time() - 10))
    assert len(process_inbox(state)) == 1
    with state.lock:
        assert len(state.store_for(slug).list("document")) == 1
        assert not any(d.original_filename == "statement.pdf" for d in state.store.list("document"))


def test_entity_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli, runner

    from taxbinder.cli import app

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    out = runner.invoke(app, ["init"]).output
    recovery = next(
        line.strip() for line in out.splitlines() if line.strip().startswith("AGE-SECRET-KEY-")
    )
    assert "added acme-trust" in cli("entity", "add", "Acme Trust", "--form", "1041")
    listing = cli("entity", "list")
    assert "household" in listing and "acme-trust" in listing and "Form 1041" in listing
    with (
        RecordStore(open_vault(tmp_path / "entities" / "acme-trust" / "vault", _keys())) as s,
        s.operation("t", "o"),
    ):
        from taxbinder.models import Taxpayer

        s.put(Taxpayer(id="trust", name="Acme Trust", kind="trust"), None)
    cli(
        "--entity",
        "acme-trust",
        "row",
        "add",
        "--year",
        "2025",
        "--institution",
        "Bank",
        "--source",
        "Trust account",
        "--doc-type",
        "1099_int",
        "--owner",
        "trust",
    )
    assert "bank.trust-account" not in cli(
        "status", "--year", "2025", "--all", "--outstanding"
    )  # household
    assert "bank.trust-account" in cli(
        "--entity", "acme-trust", "status", "--year", "2025", "--all", "--outstanding"
    )
    restored = runner.invoke(app, ["restore-key"], input=recovery + "\n").output
    assert "key restored for" in restored and "acme-trust" in restored


def test_rename_household_and_entities(shared: AppState) -> None:  # noqa: F811
    from test_sharing import local

    from taxbinder import settings

    settings.change(shared.store, "test", "other_filers", "on")  # off on a fresh vault
    mac = local(shared)
    assert 'data-rename="household"' in mac.get("/").text
    r = mac.post(
        "/api/entity/rename", json={"slug": "household", "name": "  Quill Family "}, headers=ORIGIN
    )
    assert r.status_code == 200 and r.json() == {"entity": "household", "name": "Quill Family"}
    html = mac.get("/").text
    assert "<b>Quill Family</b>" in html and "Quill Family · Form 1040" in html
    with shared.lock:
        info = entity_info(shared.store)
        assert (info.name, info.form) == ("Quill Family", "1040")  # form kept
        assert shared.store.vault.history.log()[0][1] == "entity rename Household → Quill Family"
    slug = _corp(shared)
    assert (
        mac.post(
            "/api/entity/rename", json={"slug": slug, "name": "Northwind Inc."}, headers=ORIGIN
        ).status_code
        == 200
    )
    with shared.lock:
        assert entity_info(shared.store_for(slug)).name == "Northwind Inc."
    for body, code in [
        ({"slug": "household", "name": "  "}, 400),
        ({"slug": "household", "name": "x" * 81}, 400),
        ({"slug": "nope", "name": "X"}, 404),
    ]:
        assert mac.post("/api/entity/rename", json=body, headers=ORIGIN).status_code == code, body
    sam = pair(shared)
    assert 'data-rename="' not in sam.get("/").text
    assert (
        sam.post(
            "/api/entity/rename", json={"slug": "household", "name": "X"}, headers=LAN_ORIGIN
        ).status_code
        == 403
    )


def test_rename_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    cli("init")
    cli("entity", "add", "Acme Trust", "--form", "1041")
    assert "renamed household to Quill Family" in cli("entity", "rename", "Quill Family")
    assert "renamed acme-trust to Acme Family Trust" in cli(
        "--entity", "acme-trust", "entity", "rename", "Acme Family Trust"
    )
    listing = cli("entity", "list")
    assert "Quill Family · Form 1040" in listing and "Acme Family Trust · Form 1041" in listing
    assert "can't be empty" in cli("entity", "rename", " ", ok=False)
