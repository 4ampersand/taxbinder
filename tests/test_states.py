"""The state table: every state and DC, their dates, and the rules using them."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from test_rules import ctx, found, payment

from taxbinder.models import YearConfig
from taxbinder.rules.params import load_params
from taxbinder.states import business_day, holidays, schedule, table

CODES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def test_every_state_and_dc_with_its_source() -> None:
    t = table()
    assert set(t) == CODES and len(CODES) == 51
    for s in t.values():
        assert s.page.startswith("https://"), s.code
        assert s.income_tax or s.note, s.code  # "no income tax" always says what it means
        assert not s.reviewed  # drafted, not reviewed: a person sets reviewed_by
        assert len(s.quarters) == len(s.estimated)
        if s.shares:
            assert abs(sum(s.shares) - 1) < 1e-9 and len(s.shares) == len(s.estimated)
        for year in (2025, 2026):
            for d in schedule(s.code, year).due if s.estimated else []:
                assert d.weekday() < 5 and d not in holidays(d.year), (s.code, d)
    assert (t["CA"].quarters, t["IA"].estimated[-1]) == ((1, 2, 4), (1, 31))
    assert {c for c, s in t.items() if not s.income_tax} == {
        "AK", "FL", "NV", "NH", "SD", "TN", "TX", "WA", "WY"
    }  # fmt: skip


def test_working_out_a_years_dates() -> None:
    assert business_day(date(2025, 6, 15)) == date(2025, 6, 16)  # a Sunday
    assert business_day(date(2026, 1, 31)) == date(2026, 2, 2)  # Iowa's Q4, on a Saturday
    assert date(2029, 1, 15) in holidays(2029)  # Martin Luther King Jr. Day
    ny = schedule("NY", 2025)
    assert ny.due == [date(2025, 4, 15), date(2025, 6, 16), date(2025, 9, 15), date(2026, 1, 15)]
    assert not ny.reviewed
    assert schedule("VA", 2026).due[0] == date(2026, 5, 1)
    assert schedule("TX", 2025) is None and schedule("ID", 2025) is None  # none required


def test_reviewed_dates_stay_exactly() -> None:
    for year in (2025, 2026):
        params = load_params(year)
        s = schedule("federal", year, params)
        assert s.reviewed and s.due == params.values["R-PAY-03"]["due"]["federal"]
        assert s.quarters == params.values["R-PAY-03"]["quarters"]["federal"]
        ca = schedule("CA", year, params)  # a state's dates: from the table, worked out
        assert ca.quarters == [1, 2, 4] and len(ca.due) == 3 and not ca.reviewed


def test_a_new_york_household() -> None:
    ny = YearConfig(year=2025, resident_states=["NY"])
    claims = [
        payment("NY", "2025-04-14", 3000),
        payment("NY", "2025-09-12", 3000, "estimated_q3"),
        payment("NY", "2026-01-14", 3000, "estimated_q4"),
    ]
    findings = found(ctx(year_config=ny, claims=claims), "R-PAY-03")
    [missing] = [f for f in findings if f.id.endswith("NY.q2")]
    assert "due 2025-06-16" in missing.message and "IT-2105.9" in missing.message
    assert "New York's dates aren't reviewed yet" in missing.message
    everything = " ".join(f.message for f in findings)
    assert "California" not in everything and "FTB" not in everything


def test_a_texas_household_and_a_w2_only_one() -> None:
    tx = YearConfig(year=2025, resident_states=["TX"])
    assert found(ctx(year_config=tx), "R-PAY-03") == []
    assert found(ctx(year_config=YearConfig(year=2025)), "R-PAY-03") == []  # no payments


def test_the_payments_page(tmp_path: Path) -> None:
    from helpers import new_vault
    from test_web import client

    from taxbinder.store import RecordStore
    from taxbinder.web.app import AppState

    store = RecordStore(new_vault(tmp_path / "vault"))
    state = AppState(
        vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
        today=lambda: date(2026, 3, 2),
    )  # fmt: skip
    c = client(state)
    page = c.get("/payments?year=2025").text
    assert "Estimated tax due dates · 2025" in page and "Your state isn't known yet" in page
    with store.operation("test", "states"):
        store.put(YearConfig(year=2025, resident_states=["NY", "TX"]), None)
    page = c.get("/payments?year=2025").text
    assert "New York" in page and "Jun 16" in page and "IT-2105" in page and "not reviewed" in page
    assert "Texas doesn&#39;t tax wages or investment income." in page
    assert "California" not in page.split("Estimated tax due dates")[1].split("</section>")[0]
    state.close_all()
    store.close()
