"""Check: questions are never guessed, every answer is undoable,
"Looks good, file them" is one change per vault, and a filing that stops half-way leaves
nothing half-filed (fictional data, PDFs made in the test)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from samples.generate import _render, plan, returns
from samples.household import ENTITIES, MISSING_2024
from test_web import ORIGIN, PORT, client

from taxbinder import check, review
from taxbinder.entities import create_entity
from taxbinder.ingest.inbox import ingest_file
from taxbinder.models import Taxpayer
from taxbinder.models.registry import Portal
from taxbinder.store import RecordStore, keys_from_env
from taxbinder.web.app import AppState

NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)


def drop(store: RecordStore, name: str, data: bytes) -> str:
    path = store.vault.inbox / name
    path.write_bytes(data)
    return ingest_file(store, path, "upload", "test", now=NOW, waiting=True).doc_id


def k1(n: int, issuer: str = "Fernhollow properties", year: int = 2025) -> bytes:
    return make_pdf([f"Schedule K-1 (Form 1065) {year}", issuer, f"Partner number {n}"])


def snapshot(store: RecordStore) -> dict[str, bytes]:
    """Every record file but the re-checks' findings (they're worked out again each time)."""
    root = store.vault.records
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*.yaml")
        if ".git" not in p.parts and not p.name.endswith("findings.yaml")
    }


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s, "test")
    yield s
    s.close()


def test_questions_are_never_guessed(store: RecordStore) -> None:
    sure = drop(store, "k1.pdf", k1(1))
    copy = drop(store, "k1 again.pdf", k1(1) + b"\n% saved again\n")  # same form, other bytes
    blank = drop(store, "letter.pdf", make_pdf(["Dear friend", "Thank you for your visit"]))
    shared = drop(store, "int.pdf", make_pdf(["Form 1099-INT 2025", "Lindqvist Regional Bank"]))
    with store.operation("test", "unclear and scanned"):
        d, rev = store.get("document", shared)
        r = (
            d.recipient.model_copy(update={"printed": "Quill", "ask": True})
            if d.recipient
            else None
        )
        from taxbinder.models.document import Recipient

        store.put(
            d.model_copy(update={"recipient": r or Recipient(printed="Quill", ask=True)}), rev
        )
    found = check.report(store)
    asked = {f.doc.id: f.question for f in found.asked}
    assert asked[copy] == "copy" and asked[blank] == "type" and asked[shared] == "whose"
    assert sure not in asked  # the first of the two, read clearly, looks right
    with pytest.raises(ValueError, match="answer the 3 questions first"):
        check.confirm(store, "test")

    check.mark(store, copy, "distinct", "test")  # they're different
    check.mark(store, blank, "not_tax", "test")
    review.edit_document(store, shared, "test", "1099_int", 2025)
    from taxbinder import whose

    whose.set_whose(store, shared, ["primary"], "test")
    assert check.report(store).asked == []
    sha = store.completed[-1].sha
    store.undo(sha, "test")  # each answer undoes on its own
    assert {f.question for f in check.report(store).asked} == {"whose"}


def test_scans_and_returns_are_looked_at(store: RecordStore) -> None:
    doc = drop(store, "k1.pdf", k1(2))
    with store.operation("test", "as if scanned"):
        d, rev = store.get("document", doc)
        store.put(d.model_copy(update={"ocr": "0" * 64}), rev)
    assert [f.question for f in check.report(store).asked] == ["scan"]
    check.mark(store, doc, "ok", "test")
    assert check.report(store).asked == []


def test_edit_merge_and_undo(store: RecordStore) -> None:
    doc = drop(store, "k1.pdf", k1(3, "Corbel Net Lease"))
    before = snapshot(store)
    check.edit(store, doc, "test", issuer="Brightwater Studio", account="4410-2291")
    c = store.get("document", doc)[0].classification
    assert c.account_number == "4410-2291" and c.issuer == "brightwater-studio"
    found = check.report(store).groups
    assert [(g.issuer, g.items[0].account) for g in found] == [("Brightwater Studio", "…2291")]
    store.undo(store.completed[-1].sha, "test")
    assert snapshot(store) == before
    # A new spelling read as its own place: merged into the one the binder knows.
    check.edit(store, doc, "test", issuer="Corbel Net Leasing Company")
    new = store.get("document", doc)[0].classification.issuer
    assert new != "corbel-net-lease"
    before = snapshot(store)
    assert check.merge(store, new, "corbel-net-lease", "test") == 1
    assert new not in {i.id for i in store.list("institution")}
    assert store.get("document", doc)[0].classification.issuer == "corbel-net-lease"
    assert "Corbel Net Leasing Company" in store.get("institution", "corbel-net-lease")[0].aliases
    with pytest.raises(ValueError, match="checklist rows"):
        check.merge(store, "tidemark", "corbel-net-lease", "test")  # it has accounts already
    store.undo(store.completed[-1].sha, "test")
    assert snapshot(store) == before


def test_a_corrected_sender_nothing_uses_goes(store: RecordStore) -> None:
    doc, other = drop(store, "k1.pdf", k1(4)), drop(store, "k1-b.pdf", k1(5))
    for d in (doc, other):
        check.edit(store, d, "test", issuer="Heronsgate Holdngs")  # as it was misread
    misread = store.get("document", doc)[0].classification.issuer
    assert misread == store.get("document", other)[0].classification.issuer
    check.edit(store, doc, "test", issuer="Corbel Net Lease")
    assert misread in {i.id for i in store.list("institution")}  # the other document has it
    before = snapshot(store)
    check.edit(store, other, "test", issuer="Corbel Net Lease")
    assert misread not in {i.id for i in store.list("institution")}
    store.undo(store.completed[-1].sha, "test")  # one change: the sender comes back too
    assert snapshot(store) == before
    check.edit(store, other, "test", issuer="Fernhollow properties")  # one with accounts stays
    check.edit(store, other, "test", issuer="Corbel Net Lease")
    assert "fernhollow-properties" in {i.id for i in store.list("institution")}
    check.edit(store, other, "test", issuer="Heronsgate Holdngs")  # one with a portal stays
    inst, rev = store.get("institution", misread)
    with store.operation("test", "portal"):
        store.put(inst.model_copy(update={"portal": Portal(url="https://h.example")}), rev)
    check.edit(store, other, "test", issuer="Corbel Net Lease")
    assert misread in {i.id for i in store.list("institution")}


def test_file_them_is_one_change_per_vault_and_undoes(tmp_path: Path) -> None:
    vault = new_vault(tmp_path / "app" / "vault")
    house = RecordStore(vault)
    build(house, "test")
    slug = create_entity(vault.root, keys_from_env(), "Quill Family Trust", "1041", "test")
    state = AppState(vault_path=vault.root, store=house, port=PORT, emit=lambda e: None)
    trust = state.store_for(slug)
    from taxbinder.models import Institution

    with trust.operation("test", "the trust"):
        trust.put(Taxpayer(id="trust", name="Quill Family Trust", kind="trust"), None)
        trust.put(Institution(id="tidemark", name="Tidemark"), None)
    for s, issuer in ((house, "Corbel Net Lease"), (trust, "Tidemark")):
        drop(s, "k1.pdf", k1(5, issuer))
        drop(s, "k1-2.pdf", k1(6, issuer, 2024))
    from taxbinder.rules.reconcile import reconcile

    for s in (house, trust):  # the re-checks as they'd be anyway (they run after filing too)
        for y in (2024, 2025):
            try:
                reconcile(s, y, "test")
            except ValueError:
                pass
    befores = {"house": snapshot(house), "trust": snapshot(trust)}
    c = client(state)
    shas = {}
    for name, entity in (("house", "household"), ("trust", slug)):
        assert c.post("/api/entity", json={"slug": entity}, headers=ORIGIN).status_code == 200
        assert "Looks good, file them" in c.get("/check").text
        r = c.post("/api/check/confirm", headers=ORIGIN)
        assert r.status_code == 200 and r.json()["filed"] == 2, r.text
        shas[name] = r.json()["undo"]["sha"]
    for s in (house, trust):
        assert not check.waiting(s)
        on_rows = {x.doc for e in s.list("expectation") for x in e.evidence}
        assert {d.id for d in s.list("document")} <= on_rows  # each on a checklist row
    assert c.post("/api/undo", json={"sha": shas["trust"]}, headers=ORIGIN).status_code == 200
    assert snapshot(trust) == befores["trust"] and len(check.waiting(trust)) == 2
    assert not check.waiting(house)  # the household's filing stands until undone there
    c.post("/api/entity", json={"slug": "household"}, headers=ORIGIN)
    assert c.post("/api/undo", json={"sha": shas["house"]}, headers=ORIGIN).status_code == 200
    assert snapshot(house) == befores["house"]
    state.close_all()
    house.close()


def test_a_filing_stopped_half_way_leaves_nothing_half_filed(
    store: RecordStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    drop(store, "a.pdf", k1(7, "Corbel Net Lease"))
    drop(store, "b.pdf", k1(8, "Brackley Wealth"))
    before = snapshot(store)
    calls = []

    def stop_on_second(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the app stopped")
        return real(*args, **kwargs)

    real = check.link_document
    monkeypatch.setattr(check, "link_document", stop_on_second)
    with pytest.raises(RuntimeError):
        check.confirm(store, "test")
    assert snapshot(store) == before and len(check.waiting(store)) == 2
    assert not store.vault.history.unsaved()


def test_the_return_shows_whats_listed_and_missing(tmp_path: Path) -> None:
    store = RecordStore(new_vault(tmp_path / "vault"))
    with store.operation("test", "household"):
        store.put(Taxpayer(id="primary", name="Avery Quill"), None)
    docs = plan(random.Random(7))
    (packet, _), _, (bare, _) = returns(docs)
    ids = {e[0] for e in ENTITIES}
    k1s = [d for d in docs if d.year == 2024 and d.issuer in ids and not d.corrected]
    assert len(k1s) == 12
    for i, d in enumerate(k1s):
        drop(store, f"k1-{i}.pdf", _render(d, random.Random(i)))
    ret = drop(store, "1040.pdf", packet)
    found = check.report(store)
    ((f, year, listed, totals),) = found.returns
    assert f.doc.id == ret and year == 2024  # held against its own year: its K-1s are here
    missing = {s.item.name for s in listed if not s.here}
    names = dict((e[0], e[1]) for e in ENTITIES)
    assert {names[m] for m in MISSING_2024} <= missing
    assert not {names[d.issuer] for d in k1s} & missing
    assert "You had W-2 wages: add your employers." in totals
    store.close()

    alone = RecordStore(new_vault(tmp_path / "bare"))
    drop(alone, "1040.pdf", bare)
    ((_, _, listed, totals),) = check.report(alone).returns
    assert listed == [] and "You had W-2 wages: add your employers." in totals  # no employer
    alone.close()


def test_filed_documents_are_read_for_across_the_years(store: RecordStore) -> None:
    """Filing from Check makes the rows; their amounts are read at once, not only after "Read the
    documents again" (seen on a new binder: every year said "here" and nothing more)."""
    from kformgen import form

    from taxbinder import matrix

    data = form([(330, 100, 140, "1", "Ordinary business income (loss)", "4,321."),
                 (470, 100, 130, "14", "Self-employment earnings (loss)", ""),
                 (330, 130, 140, "2", "Net rental real estate income (loss)", ""),
                 (470, 130, 130, "15", "Credits", "")],
                ["Schedule K-1 (Form 1065) 2025", "OMB No. 1545-0123", "Saltmarsh Holdings LP"])  # fmt: skip
    doc = drop(store, "saltmarsh.pdf", data)
    for f in check.report(store).asked:
        if f.question == "issuer":
            check.edit(store, f.doc.id, "test", issuer="Saltmarsh Holdings LP")
        else:
            check.mark(store, f.doc.id, "ok", "test")
    assert check.report(store).asked == []
    check.confirm(store, "test")
    row = next(r for r in matrix.rows(store) if r.cells.get(2025) and r.cells[2025].doc == doc)
    assert row.cells[2025].suggested[row.boxes.index("1")] == 4321


def test_not_filed_rows_can_be_brought_back_changed_or_deleted(tmp_path: Path) -> None:
    """A skipped document under Not filed keeps its buttons; Undo isn't the only way back."""
    vault = new_vault(tmp_path / "app" / "vault")
    store = RecordStore(vault)
    build(store, "test")
    state = AppState(vault_path=vault.root, store=store, port=PORT, emit=lambda e: None)
    c = client(state)
    skipped, not_tax = drop(store, "a.pdf", k1(1)), drop(store, "b.pdf", k1(2))
    check.mark(store, skipped, "skip", "test")
    check.mark(store, not_tax, "not_tax", "test")
    rows = c.get("/check").text.split("Not filed")[1]
    assert rows.count('data-what="back"') == 2 and rows.count("chk-del") == 2
    assert rows.count('data-what="not_tax"') == 1  # only the skipped one
    # Bring back: with the others again; Undo skips it again
    before = snapshot(store)
    assert c.post("/api/check/mark", json={"id": skipped, "what": "back"}, headers=ORIGIN).is_success
    assert skipped not in {f.doc.id for f in check.report(store).later}
    store.undo(store.completed[-1].sha, "test")
    assert snapshot(store) == before
    # Change… on a Not filed row: the change and the bringing back are one change
    r = c.post("/api/check/edit", json={"id": not_tax, "back": True, "tax_year": "2024"},
               headers=ORIGIN)  # fmt: skip
    assert r.is_success, r.text
    assert not_tax not in {f.doc.id for f in check.report(store).later}
    assert store.get("document", not_tax)[0].classification.tax_year == 2024
    store.undo(store.completed[-1].sha, "test")
    assert snapshot(store) == before
    # Delete, from any row on Check
    assert c.post("/api/documents/delete", json={"id": skipped}, headers=ORIGIN).is_success
    assert skipped not in {f.doc.id for f in check.report(store).later}
    store.undo(store.completed[-1].sha, "test")
    assert snapshot(store) == before
    asked = c.get("/check").text.split("Questions for you")
    assert len(asked) == 1 or "chk-del" in asked[1]
