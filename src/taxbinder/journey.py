"""The journey: Start · Add documents · Check · Keep it safe · Results · Files for your
preparer, each done or not yet, with a badge for what changed (the top bar's Check), and the
next thing to do (where the app opens). Files for your preparer is done once the .zip is made,
until documents arrive after it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from taxbinder import backup, perf, safe
from taxbinder.household import worked_out
from taxbinder.store import RecordStore


@dataclass
class Step:
    id: str
    label: str
    href: str
    done: bool = False
    badge: str = ""
    news: bool = False  # the badge asks for something (questions, findings), not just counts


# The page a template belongs to, for "you are here".
PAGES = {
    "start.html": "start",
    "add.html": "add",
    "check.html": "check",
    "backup.html": "safe",
    "preparer.html": "handoff",
}
BINDER = {"documents.html", "payments.html", "donations.html", "foreign.html", "history.html"}


def questions(store: RecordStore, year: int) -> int:
    """What Check asks: the household until confirmed, and each document it can't be sure of."""
    from taxbinder import check
    from taxbinder.entities import entity_info

    asked = len(check.report(store).asked)

    household = entity_info(store).form in ("1040", "1040-SR")  # an entity has no household box
    return asked + (household and not worked_out(store, year).confirmed)


@perf.timed("journey.steps")
def steps(
    store: RecordStore, year: int, vault: Path, today: date, remote: bool = False
) -> list[Step]:
    from taxbinder.report import year_status

    rows = [e for e in store.list("expectation", year) if e.applicability == "applicable"]
    to_come = sum(e.acquisition not in ("vaulted", "superseded") for e in rows)
    have = any(d.classification.tax_year == year for d in store.list("document"))
    asked = questions(store, year)
    from taxbinder import check

    new = sum(check.SKIPPED not in d.tags for d in check.waiting(store))  # not yet filed
    status = year_status(store, year, today)
    counts = status.counts()
    look = counts["error"] + counts["warning"] if status.checked else 0
    kept = safe.done(vault)  # the recovery key saved and a backup made
    years = sorted({e.year for e in store.list("expectation")} | {year})
    from taxbinder.package import handoff

    made = handoff.last(store, year)
    stale = bool(made and made.new)  # documents arrived after the .zip was made

    out = [
        Step("start", "Start", "/", done=True),
        Step("add", "Add documents", "/add", done=have,
             badge=f"{to_come} to come" if to_come else ""),
        Step("check", "Check", "/check", done=have and not asked and not new,
             badge=f"{asked} question{'s' if asked != 1 else ''}" if asked
             else f"{new} new" if new else "", news=True),  # questions first
        Step("safe", "Keep it safe", "/backup", done=kept),
        Step("results", "Results", "/", done=have and not to_come and not counts["error"],
             badge=f"{look} to look at" if look else "", news=counts["error"] > 0),
        Step("handoff", "Files for your preparer", "/preparer", done=bool(made) and not stale,
             badge="out of date" if stale else "", news=stale),
    ]  # fmt: skip
    # Keep it safe: until it's done, and in the vault's first year; never from a phone.
    if remote or (kept and year != years[0]):
        out = [s for s in out if s.id != "safe"]
    return out


def preview() -> list[Step]:
    """The journey on the Start page, before there's a vault: every step still to come."""
    labels = {"start": "Start", "add": "Add documents", "check": "Check", "safe": "Keep it safe",
              "results": "Results", "handoff": "Files for your preparer"}  # fmt: skip
    return [Step(k, v, "/") for k, v in labels.items()]


def next_step(all_steps: list[Step], later: bool = False) -> Step:
    """The first step not done, from Add documents on; Results once they all are. `later`: Keep
    it safe was put off ("Remind me later"), so it's skipped for now."""
    for s in all_steps:
        if s.id == "safe" and later:
            continue
        if s.id in ("add", "check", "safe") and not s.done:
            return s
    return next(s for s in all_steps if s.id == "results")


def last_backup(vault: Path, today: date) -> str:
    info = backup.last(vault)
    if not info:
        return "never"
    try:
        days = (today - datetime.fromisoformat(info["at"]).date()).days
    except (KeyError, ValueError):
        return "never"
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"
