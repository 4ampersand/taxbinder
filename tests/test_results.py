"""Results' questions: only with a reason, never when a document answers it, at
most 3, "No" remembered for the year and undoable (the invented household; PDFs made here)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from helpers import new_vault
from samples.generate import _form_1040, _render, _text_pdf, plan, returns
from test_check import drop

from taxbinder import results
from taxbinder.models import Taxpayer
from taxbinder.store import RecordStore

DOCS = plan(random.Random(7))
PACKET = returns(DOCS)[0][0]  # the 2024 return: estimated payments, Schedule A taxes and gifts


def _kinds(store: RecordStore) -> list[str]:
    return [a.kind for a in results.questions(store, 2025)[0]]


@pytest.fixture
def store(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    with s.operation("test", "household"):
        s.put(Taxpayer(id="primary", name="Avery Quill"), None)
    yield s
    s.close()


def test_no_questions_for_a_w2_household(store: RecordStore) -> None:
    w2_only = _text_pdf([_form_1040(2024, [("1a Total amount from Form(s) W-2, box 1", 81000)])])
    drop(store, "2024 return.pdf", w2_only)  # the standard deduction: no Schedule A
    asks, known = results.questions(store, 2025)
    assert asks == [] and known == []


def test_each_question_has_its_reason_and_at_most_3(store: RecordStore) -> None:
    drop(store, "2024 return.pdf", PACKET)
    asks, _ = results.questions(store, 2025)
    assert [a.kind for a in asks] == ["estimated", "property_tax", "vehicle"]  # gifts wait
    assert asks[0].why == "Your 2024 return shows estimated tax payments."
    assert all(a.why for a in asks)
    results.answer(store, 2025, "estimated", "no", "test")
    assert _kinds(store) == ["property_tax", "vehicle", "gifts"]  # the 4th comes up
    store.undo(store.completed[-1].sha, "test")  # "No" undone: asked again
    assert _kinds(store)[0] == "estimated"
    results.answer(store, 2025, "vehicle", "unsure", "test")  # Not sure, ask my preparer
    assert "vehicle" not in _kinds(store)
    assert store.get("year", "2025")[0].asked == {"vehicle": "unsure"}  # for 2025 only
    with pytest.raises(ValueError):
        results.answer(store, 2025, "estimated", "maybe", "test")


def test_none_about_property_tax_with_an_escrowed_1098(store: RecordStore) -> None:
    drop(store, "2024 return.pdf", PACKET)  # it deducted real estate taxes
    mortgage = next(d for d in DOCS if d.doc_type == "1098" and d.year == 2025)
    drop(store, "1098.pdf", _render(mortgage, random.Random(1)))
    asks, known = results.questions(store, 2025)
    assert "property_tax" not in [a.kind for a in asks]
    assert known == ["Property tax 2025: $6,100, from your Gannet Home Lending mortgage statement"]


def test_what_doesnt_come_in_the_mail(store: RecordStore) -> None:
    """Every common thing no form reports, with last year's, this year's so far and the
    answer; "None this year" kept for the year and taken back again, each undoable."""
    assert [m.kind for m in results.mail(store, 2025)] == ["gifts", "estimated", "property_tax",
                                                           "vehicle", "foreign"]  # fmt: skip
    assert all(m.before == "" and m.added == 0 for m in results.mail(store, 2025))  # nothing known
    drop(store, "2024 return.pdf", PACKET)
    by = {m.kind: m for m in results.mail(store, 2025)}
    assert by["estimated"].before == "on your 2024 return" and by["estimated"].answer == ""
    results.answer(store, 2025, "vehicle", "no", "test")
    assert {m.kind: m.answer for m in results.mail(store, 2025)}["vehicle"] == "no"
    results.answer(store, 2025, "vehicle", "", "test")  # Change: asked again
    assert {m.kind: m.answer for m in results.mail(store, 2025)}["vehicle"] == ""
    store.undo(store.completed[-1].sha, "test")  # and that's undoable too
    assert store.get("year", "2025")[0].asked == {"vehicle": "no"}
