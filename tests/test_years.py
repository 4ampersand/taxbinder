"""Start a year from another; add and delete checklist rows (shared by CLI and app)."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build

from taxbinder.ingest.inbox import ingest_file
from taxbinder.review import set_verified
from taxbinder.rules.params import load_params
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordNotFound, RecordStore, fsck
from taxbinder.years import add_row, carry_over, delete_row, edit_row, new_year, years_in_vault

KETTLE = "2025/fernhollow-properties.kettle-410.k1"


@pytest.fixture
def store(tmp_path: Path):
    vault = new_vault(tmp_path / "vault")
    s = RecordStore(vault)
    build(s, "test")
    yield s
    s.close()


def _attach_k1(store: RecordStore, tmp_path: Path) -> None:
    pdf = store.vault.inbox / "Kettle410_2025_K1.pdf"
    pdf.write_bytes(
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    )
    assert ingest_file(store, pdf, "upload", "test").linked_to == KETTLE


def test_new_year_copies_rows_with_statuses_reset(store: RecordStore, tmp_path: Path) -> None:
    _attach_k1(store, tmp_path)
    set_verified(store, [KETTLE], True, "checked", "test")
    rows_2025 = store.list("expectation", 2025)

    assert new_year(store, 2026, 2025, "test") == len(rows_2025)
    rows_2026 = {e.id: e for e in store.list("expectation", 2026)}
    assert set(rows_2026) == {e.id.replace("2025/", "2026/", 1) for e in rows_2025}
    kettle = rows_2026["2026/fernhollow-properties.kettle-410.k1"]
    assert (kettle.applicability, kettle.acquisition, kettle.review, kettle.delivery) == (
        "applicable",
        "expected",
        "unreviewed",
        "not_sent",
    )
    assert kettle.evidence == [] and kettle.events == []
    deferred = [e for e in rows_2025 if e.applicability != "applicable"]
    assert deferred and all(
        rows_2026[e.id.replace("2025/", "2026/", 1)].applicability == "applicable" for e in deferred
    )
    year_2025, _ = store.get("year", "2025")
    year_2026, _ = store.get("year", "2026")
    assert year_2026.resident_states == year_2025.resident_states
    assert year_2025.extension_filed and not year_2026.extension_filed
    assert year_2026.summaries == []
    assert years_in_vault(store) == [2025, 2026]
    reconcile(store, 2026, "test")  # 2026 has rule parameters
    assert fsck(store.vault) == []


def test_new_year_twice_creates_nothing_twice(store: RecordStore) -> None:
    n = new_year(store, 2026, 2025, "test")
    rows = {e.id: e for e in store.list("expectation", 2026)}
    assert new_year(store, 2026, 2025, "test") == 0
    assert {e.id: e for e in store.list("expectation", 2026)} == rows and len(rows) == n
    with pytest.raises(ValueError, match="2019 has no checklist rows"):
        new_year(store, 2027, 2019, "test")


def test_a_year_with_a_few_rows_gets_the_rest(store: RecordStore) -> None:
    mine = add_row(store, 2026, "Fernhollow properties", "Kettle 410", "k1_1065", "primary", "t")
    edit_row(store, mine, "t", comment="added early")
    n = new_year(store, 2026, 2025, "test")
    assert n == len(store.list("expectation", 2025)) - 1
    assert store.get("expectation", mine)[0].comment == "added early"  # left as it was


def test_drop_rows_that_wont_recur(store: RecordStore) -> None:
    cirrus = "2025/fernhollow-properties.cirrus.k1"
    assert all(c.keep for c in carry_over(store, 2026, 2025))
    new_year(store, 2026, 2025, "test", drop={cirrus})
    assert "2026/fernhollow-properties.cirrus.k1" not in {
        e.id for e in store.list("expectation", 2026)
    }
    src, _ = store.get("source", "fernhollow-properties.cirrus")
    assert src.last_tax_year == 2025
    # Starting again suggests the same drop, and a row added by hand brings it back.
    suggested = {c.row.id: c for c in carry_over(store, 2026, 2025)}
    assert not suggested[cirrus].keep and suggested[cirrus].why == "last one was for 2025"
    add_row(store, 2026, "Fernhollow properties", "Cirrus", "k1_1065", "primary", "t")
    assert store.get("source", src.id)[0].last_tax_year is None


def test_suggested_drops(store: RecordStore) -> None:
    from datetime import date

    src, rev = store.get("source", "fernhollow-properties.cirrus")
    with store.operation("t", "closed"):
        store.put(src.model_copy(update={"closed": date(2025, 8, 1)}), rev)
    why = {c.row.source: c.why for c in carry_over(store, 2026, 2025) if not c.keep}
    assert why == {"fernhollow-properties.cirrus": "closed 2025-08-01"}
    new_year(store, 2026, 2025, "test")  # no drop given: the suggested ones are left out
    assert not [e for e in store.list("expectation", 2026) if e.source == src.id]


def test_a_move_last_year_isnt_a_move_this_year(store: RecordStore) -> None:
    from datetime import date

    y, rev = store.get("year", "2025")
    with store.operation("t", "moved"):
        store.put(
            y.model_copy(update={"resident_states": ["CA", "NY"], "moved_on": date(2025, 7, 1)}),
            rev,
        )
    new_year(store, 2026, 2025, "test")
    new, _ = store.get("year", "2026")
    assert (new.resident_states, new.moved_on) == (["NY"], None)


def test_nothing_from_last_year_counts(store: RecordStore, tmp_path: Path) -> None:
    from datetime import date
    from decimal import Decimal

    from taxbinder.claims import add_payment
    from taxbinder.web.views import progress

    edit_row(store, KETTLE, "t", amount=Decimal("1234.50"), comment="last year's")
    add_payment(store, "federal", Decimal(500), date(2025, 4, 15), "estimated_q1", "t")
    new_year(store, 2026, 2025, "test")
    _attach_k1(store, tmp_path)  # a 2025 K-1 arriving now goes to the 2025 row
    kettle, _ = store.get("expectation", "2026/fernhollow-properties.kettle-410.k1")
    assert (kettle.amount, kettle.comment, kettle.evidence) == (None, "", [])
    assert store.list("claim", 2026) == []
    p = progress(store, 2026)
    assert (p["have"], p["verified"]) == (0, 0)


def test_add_row_new_and_existing_sources(store: RecordStore) -> None:
    new = add_row(store, 2025, "Fidelis Investments", "Rollover IRA", "1099_r", "primary", "t")
    assert new == "2025/fidelis-investments.rollover-ira.1099-r"
    inst, _ = store.get("institution", "fidelis-investments")
    src, _ = store.get("source", "fidelis-investments.rollover-ira")
    assert inst.name == "Fidelis Investments" and src.owner == "primary"
    assert [p.doc_type for p in src.produces] == ["1099_r"]

    # An existing source gains a document type; ids match the importer's.
    other = add_row(store, 2025, "Fernhollow properties", "Kettle 410", "k1_state", "primary", "t")
    assert other == "2025/fernhollow-properties.kettle-410.k1-state"
    kettle_src, _ = store.get("source", "fernhollow-properties.kettle-410")
    assert {p.doc_type for p in kettle_src.produces} >= {"k1_1065", "k1_state"}

    with pytest.raises(ValueError, match="already exists"):
        add_row(store, 2025, "Fernhollow properties", "Kettle 410", "k1_1065", "primary", "t")
    with pytest.raises(RecordNotFound):
        add_row(store, 2025, "X", "Y", "w2", "nobody", "t")
    with pytest.raises(ValueError, match="required"):
        add_row(store, 2025, " ", "Y", "w2", "primary", "t")


def test_delete_row(store: RecordStore, tmp_path: Path) -> None:
    _attach_k1(store, tmp_path)
    with pytest.raises(ValueError, match="attached file"):
        delete_row(store, KETTLE, "test")
    gone = "2025/fernhollow-properties.cirrus.k1"
    delete_row(store, gone, "test")
    with pytest.raises(RecordNotFound):
        store.get("expectation", gone)
    store.get("source", "fernhollow-properties.cirrus")  # the source stays for other years
    new_year(store, 2026, 2025, "test")
    assert "2026/fernhollow-properties.cirrus.k1" not in {
        e.id for e in store.list("expectation", 2026)
    }


def test_missing_rule_parameters_are_a_clear_error() -> None:
    assert load_params(2026).values["R-EXP-01"]["deadline"].isoformat() == "2027-04-15"
    with pytest.raises(ValueError, match="no rule parameters for 2031"):
        load_params(2031)


def test_amounts_and_comments(store: RecordStore) -> None:
    from decimal import Decimal

    from taxbinder.years import edit_row, parse_amount

    assert parse_amount("$1,234.5") == Decimal("1234.50") and parse_amount(" ") is None
    for bad in ("abc", "1e400", "NaN"):
        with pytest.raises(ValueError, match="not an amount"):
            parse_amount(bad)
    rid = add_row(
        store,
        2025,
        "IRS",
        "Q3 estimate",
        "payment_confirmation",
        "primary",
        "t",
        amount=Decimal("65000"),
        comment="paid early",
    )
    exp, _ = store.get("expectation", rid)
    assert (exp.amount, exp.comment) == (Decimal("65000"), "paid early")
    edit_row(store, rid, "t", amount=Decimal("4321.09"))
    edit_row(store, rid, "t", comment="")
    edit_row(store, rid, "t", comment="")  # no change, no event
    exp, _ = store.get("expectation", rid)
    assert (exp.amount, exp.comment) == (Decimal("4321.09"), "")
    assert [e.text for e in exp.events if e.type == "edited"] == ["amount", "comment"]


def test_an_earlier_year_keeps_every_row_and_ends_nothing(store: RecordStore) -> None:
    cirrus = "2025/fernhollow-properties.cirrus.k1"
    rows_2025 = store.list("expectation", 2025)
    assert new_year(store, 2024, 2025, "test", drop={cirrus}) == len(rows_2025) - 1
    assert "2024/fernhollow-properties.cirrus.k1" not in {
        e.id for e in store.list("expectation", 2024)
    }
    assert store.get("source", "fernhollow-properties.cirrus")[0].last_tax_year is None
    assert all(e.acquisition == "expected" and not e.evidence for e in store.list("expectation", 2024))
    with pytest.raises(ValueError, match="keep at least one row"):
        new_year(store, 2023, 2024, "test", drop={e.id for e in store.list("expectation", 2024)})
