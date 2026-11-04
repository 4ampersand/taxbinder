"""The 50 states and DC: income tax or not, estimated-payment dates, forms.

From `rules/states.yaml`, drafted from each state's own pages and reviewed state by state. A
year's dates are worked out from the published schedule, moved to the next business day after
a weekend or federal holiday. Dates in a reviewed parameter file (`rules/params`) win.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from importlib import resources
from typing import Any

from taxbinder.yamlio import load_yaml


@dataclass(frozen=True)
class State:
    code: str
    name: str
    income_tax: bool
    estimated: tuple[tuple[int, int], ...]  # (month, day); a Jan/Feb date is the next year
    quarters: tuple[int, ...]
    shares: tuple[float, ...] = ()
    forms: dict[str, str] = field(default_factory=dict)
    note: str = ""
    page: str = ""
    reviewed: bool = False


@dataclass(frozen=True)
class Schedule:
    """One jurisdiction's estimated-payment dates for a tax year."""

    due: list[date]
    quarters: list[int]
    reviewed: bool


@cache
def table() -> dict[str, State]:
    raw = load_yaml(resources.files("taxbinder.rules").joinpath("states.yaml").read_text())
    out = {}
    for code, s in raw.items():
        dates = tuple((int(m), int(d)) for m, d in (x.split("-") for x in s.get("estimated", [])))
        out[code] = State(
            code=code,
            name=s["name"],
            income_tax=bool(s["income_tax"]),
            estimated=dates,
            quarters=tuple(s.get("quarters") or range(1, len(dates) + 1)),
            shares=tuple(s.get("shares") or ()),
            forms={k: str(v) for k, v in (s.get("forms") or {}).items()},
            note=s.get("note", ""),
            page=s.get("page", ""),
            reviewed=bool(s.get("reviewed_by")),
        )
    return out


def _nth_monday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 7 * (n - 1))


def _last_monday(year: int, month: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=last.weekday())


def _observed(day: date) -> date:
    """A fixed holiday on a weekend is observed on the Friday before or the Monday after."""
    shift = {5: -1, 6: 1}.get(day.weekday(), 0)
    return day + timedelta(shift)


@cache
def holidays(year: int) -> frozenset[date]:
    """Federal holidays, and DC's Emancipation Day (it moves federal due dates in April)."""
    fixed = [(1, 1), (4, 16), (6, 19), (7, 4), (11, 11), (12, 25)]
    days = {_observed(date(year, m, d)) for m, d in fixed}
    days |= {
        _nth_monday(year, 1, 3),  # Martin Luther King Jr. Day
        _nth_monday(year, 2, 3),  # Washington's Birthday
        _last_monday(year, 5),  # Memorial Day
        _nth_monday(year, 9, 1),  # Labor Day
        _nth_monday(year, 10, 2),  # Columbus Day
        _nth_monday(year, 11, 4) + timedelta(3),  # Thanksgiving (4th Thursday)
    }
    return frozenset(days)


def business_day(day: date) -> date:
    while day.weekday() >= 5 or day in holidays(day.year):
        day += timedelta(1)
    return day


def schedule(code: str, year: int, params: Any = None) -> Schedule | None:  # noqa: ANN401
    """`code`'s estimated-payment dates for tax year `year`; None when it has none. `params`:
    the year's rule parameters, whose R-PAY-03 dates win (reviewed when the file is)."""
    if params is not None:
        due = params.values.get("R-PAY-03", {}).get("due", {})
        if code in due:
            quarters = params.values["R-PAY-03"].get("quarters", {}).get(code)
            quarters = list(quarters or range(1, len(due[code]) + 1))
            return Schedule(list(due[code]), quarters, params.reviewed)
    state = table().get(code)
    if state is None or not state.estimated:
        return None
    dates = [business_day(date(year + (m < 3), m, d)) for m, d in state.estimated]
    return Schedule(dates, list(state.quarters), state.reviewed)


@dataclass(frozen=True)
class Row:
    """A line of "Estimated tax due dates" on the Payments page."""

    name: str
    schedule: Schedule | None
    state: State | None  # None for federal


def due_dates(year: int, resident: list[str], params: Any = None) -> list[Row]:  # noqa: ANN401
    """Federal, then each of the household's states, for tax year `year`."""
    rows = [Row("Federal", schedule("federal", year, params), None)]
    for code in resident:
        state = table().get(code.upper())
        if state is not None:
            rows.append(Row(state.name, schedule(state.code, year, params), state))
    return rows
