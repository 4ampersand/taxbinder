"""Whose a document is, moving it to another filer, corrected forms."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from quills import build
from test_parties import form

from taxbinder.entities import create_entity, move_document, vault_path
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Taxpayer
from taxbinder.store import RecordStore, keys_from_env, open_vault
from taxbinder.web.app import AppState
from taxbinder.whose import recipient_of, set_whose

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
FAMILY = [
    Taxpayer(id="primary", name="Avery Quill", tin="900-00-0101"),
    Taxpayer(id="spouse", name="Jordan Quill"),
    Taxpayer(id="grantor-trust", name="Quill Family Trust", kind="trust"),
]


def test_whose_by_the_recipients_name() -> None:
    def who(printed: str, last4: str | None = None) -> tuple[list[str], bool]:
        r = recipient_of(printed, last4, FAMILY)
        return r.taxpayers, r.ask

    assert who("Avery Quill") == (["primary"], False)
    assert who("AVERY J QUILL") == (["primary"], False)  # a middle initial
    assert who("Avery Quill & Jordan Quill") == (["primary", "spouse"], False)
    assert who("Avery and Jordan Quill") == (["primary", "spouse"], False)
    assert who("Quill Family Trust") == (["grantor-trust"], False)
    assert who("The Quill Family Trust") == (["grantor-trust"], False)
    # unclear: asked on review, never guessed
    assert who("Hollis Quill Legacy Trust") == ([], True)  # a trust not seen before
    assert who("Quill") == ([], True)  # a surname alone
    assert who("Rowan Quill") == ([], True)  # not one of the vault's people
    assert who("Avery Quill", last4="9999") == (["primary"], True)  # not Avery's number
    assert recipient_of(None, None, FAMILY).taxpayers == []


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s, "test")
    yield s
    s.close()


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW).doc_id


def test_a_trusts_document_stays_and_an_unclear_one_is_answered(store: RecordStore) -> None:
    trust = drop(store, "t.pdf", form("Tamarack Savings Bank", recipient="Quill Family Trust"))
    doc = store.get("document", trust)[0]
    assert doc.recipient.taxpayers == ["grantor-trust"]  # in the household's vault, labelled
    unclear = drop(
        store, "u.pdf", form("Kestrel Media Group", recipient="Hollis Quill Legacy Trust")
    )
    assert store.get("document", unclear)[0].recipient.ask
    set_whose(store, unclear, [], "test", new_trust=True)
    sha = store.completed[-1].sha
    answered = store.get("document", unclear)[0].recipient
    assert answered.taxpayers == ["hollis-quill-legacy-trust"] and not answered.ask
    assert store.get("taxpayer", "hollis-quill-legacy-trust")[0].kind == "trust"
    store.undo(sha, "test")
    assert store.get("document", unclear)[0].recipient.ask
    with pytest.raises(ValueError, match="knows"):
        set_whose(store, unclear, ["nobody"], "test")


def test_moving_to_the_trusts_own_vault(tmp_path: Path) -> None:
    base = tmp_path / "vault"
    with RecordStore(new_vault(base)) as household:
        build(household, "test")
        doc = drop(
            household, "t.pdf", form("Tamarack Savings Bank", recipient="Quill Family Trust")
        )
        slug = create_entity(base, keys_from_env(), "Quill Family Trust", "1041", "test")
        with RecordStore(open_vault(vault_path(base, slug), keys_from_env())) as trust:
            move_document(household, trust, doc, "test")
            moved = trust.get("document", doc)[0]
            assert trust.blobs.get(moved.sha256)  # the file itself went too
            assert trust.get("institution", moved.classification.issuer)[0].name == (
                "Tamarack Savings Bank"
            )
            assert doc not in {d.id for d in household.list("document")}
            household.undo(household.completed[-1].sha, "test")  # back in the household's too
            assert household.get("document", doc)[0].id == doc


def test_corrected_form_with_no_checklist_row(store: RecordStore) -> None:
    # another issuer's 1099-INT: never replaced by a corrected Gannet form
    bank = drop(store, "t.pdf", form("Tamarack Savings Bank", "88120-3304", ein="00-0000103"))
    first = drop(store, "a.pdf", form("Gannet Home Lending", "77-3304"))
    other = drop(store, "b.pdf", form("Gannet Home Lending", "77-9912"))
    fixed = drop(store, "c.pdf", form("Gannet Home Lending", "77-3304", corrected=True))
    assert store.get("document", fixed)[0].version.supersedes == first  # same account, not b
    assert store.get("document", other)[0].version.supersedes is None
    assert bank not in {d.version.supersedes for d in store.list("document")}  # another issuer


def test_documents_and_checklist_pages(tmp_path: Path) -> None:
    from test_web import client, rows_of

    base = tmp_path / "vault"
    store = RecordStore(new_vault(base))
    build(store, "test")
    drop(store, "t.pdf", form("Tamarack Savings Bank", recipient="Quill Family Trust"))
    drop(store, "u.pdf", form("Kestrel Media Group", recipient="Rowan Quill"))
    state = AppState(
        vault_path=base, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    c = client(state)
    page = c.get("/documents?year=2025").text
    assert "Whose? Rowan Quill" in page and ">Quill Family Trust" in page
    assert "act-move" not in page  # the trust has no vault of its own
    create_entity(base, keys_from_env(), "Quill Family Trust", "1041", "test")
    assert "Move to Quill Family Trust’s vault" in c.get("/documents?year=2025").text
    exp = next(e for e in store.list("expectation", 2025) if e.delivery == "sent")
    _, rev = store.get("expectation", exp.id)
    with store.operation("test", "corrected after sending"):
        store.put(exp.model_copy(update={"delivery": "stale"}), rev)
    assert "needs sending again" in rows_of(c, state)
    state.close_all()
    store.close()
