"""Your binder: its data, from the invented household."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import new_vault
from quills import KETTLE, build

from taxbinder import binder
from taxbinder.models import Source
from taxbinder.models.expectation import Expectation
from taxbinder.store import RecordStore

TODAY = date(2026, 3, 2)


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    build(s)
    yield s
    s.close()


def test_every_row_of_the_year_is_a_square(store: RecordStore) -> None:
    c = binder.build(store, TODAY)
    assert c.years == [2025]
    rows = [e for e in store.list("expectation") if e.applicability == "applicable"]
    for e in rows:
        source, _ = store.get("source", e.source)
        sq = c.square(source.institution, e.doc_type, 2025)
        assert sq is not None and sq.status in ("in", "to_come"), e.id
    # issuers come grouped by their first kind (a staircase), each with its kinds
    firsts = [[k for k, _ in c.kinds].index(i.kinds[0]) for i in c.issuers]
    assert firsts == sorted(firsts) and all(i.kinds for i in c.issuers)
    assert [k for k, _ in c.kinds][0] == "w2"  # in the usual order of forms


def test_earlier_years_and_a_sender_that_stopped(store: RecordStore) -> None:
    kettle, _ = store.get("expectation", KETTLE)
    with store.operation("test", "2024"):
        store.put(Expectation(id=f"2024/{kettle.source}.k1", year=2024, source=kettle.source,
                              doc_type="k1_1065", acquisition="received", amount=1250), None)
        store.put(Source(id="lrb.savings", institution="lrb", name="Savings", owner="primary",
                         produces=[{"doc_type": "1099_int"}]), None)
        store.put(Expectation(id="2024/lrb.savings.1099-int", year=2024, source="lrb.savings",
                              doc_type="1099_int", acquisition="received", amount=88), None)
    c = binder.build(store, TODAY)
    assert c.years == [2024, 2025]
    issuer = store.get("source", kettle.source)[0].institution
    k1 = c.square(issuer, "k1_1065", 2024)
    assert (k1.status, k1.amount) == ("in", Decimal(1250))
    # came in 2024, not on 2025's list: a gap the binder shows, not just nothing
    assert c.square("lrb", "1099_int", 2025).status == "gap"
    assert c.square("lrb", "1099_int", 2024).status == "in"


def test_noticed_for_you(store: RecordStore) -> None:
    """Notes from the binder's own years, each one undoable click."""
    from taxbinder import notices
    from taxbinder.models import Institution

    with store.operation("test", "2024 and a renamed sender"):
        store.put(Source(id="lrb.savings", institution="lrb", name="Savings", owner="primary",
                         produces=[{"doc_type": "1099_int"}]), None)
        store.put(Expectation(id="2024/lrb.savings.1099-int", year=2024, source="lrb.savings",
                              doc_type="1099_int", acquisition="received", amount=88), None)
        store.put(Institution(id="tidemark-old", name="Old Tidemark Bank", tin="00-0000731"), None)
        tidemark, rev = store.get("institution", "tidemark")
        store.put(tidemark.model_copy(update={"tin": "00-0000731"}), rev)
    found = {n.id: n for n in notices.notices(store, binder.build(store, TODAY))}
    gap = found["gap:lrb:1099_int:2025"]
    assert "came in 2024" in gap.text and (gap.face, gap.at) == ("docs", "1099_int")
    same = found["ein:tidemark:tidemark-old"]  # one note per EIN, each name with its documents
    assert [n[:2] for n in same.names] == [("tidemark", "Tidemark"),
                                          ("tidemark-old", "Old Tidemark Bank")]
    assert same.actions == [("They're different", "dismiss")]

    notices.answer(store, gap.id, "add", "test")  # expected again: on 2025's list, to come
    assert binder.build(store, TODAY).square("lrb", "1099_int", 2025).status == "to_come"
    store.undo(store.completed[-1].sha, "test")
    notices.answer(store, gap.id, "stopped", "test")  # the account closed: no gap, no note
    assert binder.build(store, TODAY).square("lrb", "1099_int", 2025) is None
    assert store.get("source", "lrb.savings")[0].last_tax_year == 2024
    with pytest.raises(ValueError, match="checklist rows"):  # the one with rows can't go
        notices.answer(store, same.id, "keep:tidemark-old", "test")
    notices.answer(store, same.id, "keep:tidemark", "test")  # the name chosen is kept
    assert store.get("institution", "tidemark")[0].name == "Tidemark"
    assert "Old Tidemark Bank" in store.get("institution", "tidemark")[0].aliases
    assert not any(n.id.startswith("ein:") for n in notices.notices(store, binder.build(store, TODAY)))
    store.undo(store.completed[-1].sha, "test")
    notices.answer(store, "ein:tidemark:tidemark-old", "dismiss", "test")
    left = {n.id for n in notices.notices(store, binder.build(store, TODAY))}
    assert not left & {gap.id, "ein:tidemark:tidemark-old"}


def test_a_year_beside_the_deck(store: RecordStore) -> None:
    """A year's Check and Hand off sit beside its card."""
    from test_web import client

    from taxbinder.web.app import AppState

    state = AppState(vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
                     today=lambda: TODAY)  # fmt: skip
    c = client(state)
    html = c.get("/?year=2025").text
    front = html.split('<section class="panel" data-side="2025"')[1].split("</section>")[0]
    side = html.split('class="panel stages" data-side="2025"')[1].split("</section>")[0]
    for part in ("<h3>Check</h3>", "deadline"):
        assert part in side, part
    for part in ("<h3>Hand off", 'href="/preparer?year=2025"'):  # near the top
        assert part in front and front.index(part) < front.index("Add 2025 documents"), part
    assert "style=" not in html.split('id="page"')[1]
