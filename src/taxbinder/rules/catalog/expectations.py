"""Expectations: R-EXP-01…07."""

from __future__ import annotations

from collections.abc import Iterator

from taxbinder.rules.engine import Ctx, Flag, rule


def _applicable(ctx: Ctx):  # noqa: ANN202
    return [e for e in ctx.expectations if e.applicability == "applicable"]


@rule(
    "R-EXP-01", "Applicable expectation not verified near the deadline", "error", "missing_evidence"
)
def near_deadline(ctx: Ctx) -> Iterator[Flag]:
    deadline = ctx.filing_deadline()
    days_left = (deadline - ctx.today).days
    if days_left > max(ctx.param("R-EXP-01", "days")):
        return
    when = (
        f"{days_left} days to the {deadline} deadline"
        if days_left >= 0
        else f"deadline {deadline} passed"
    )
    for e in _applicable(ctx):
        if e.review != "verified" and e.acquisition != "superseded":
            yield Flag([e.id], f"{e.id}: not verified ({e.acquisition}); {when}")


@rule("R-EXP-02", "Received but not vaulted", "warning", "missing_evidence")
def received_not_vaulted(ctx: Ctx) -> Iterator[Flag]:
    days = ctx.param("R-EXP-02", "days")
    for e in _applicable(ctx):
        if e.acquisition != "received":
            continue
        received = [ev.at for ev in e.events if ev.type == "received"]
        on = max(received) if received else None
        on_day = on.date() if hasattr(on, "date") and callable(on.date) else on
        if on_day is None or (ctx.today - on_day).days > days:
            since = f"since {on_day}" if on_day else "(received date unknown)"
            yield Flag(
                [e.id],
                f"{e.id}: received {since} but not in the vault",
                suggested_action="Drop the file into the inbox so it is hashed and filed",
            )


@rule(
    "R-EXP-03",
    "Not-applicable decision or waiver of type other without a note",
    "error",
    "possible_inconsistency",
)
def other_without_note(ctx: Ctx) -> Iterator[Flag]:
    for e in ctx.expectations:
        if e.applicability == "other" and not (e.applicability_note or "").strip():
            yield Flag([e.id], f"{e.id}: marked 'other' without a note")
    for c in ctx.claims:
        for q in c.requirements:
            if q.waived and q.waived.reason == "other" and not q.waived.note.strip():
                yield Flag([c.id], f"{c.id}: {q.id} waived as 'other' without a note", key=q.id)


@rule("R-EXP-04", "New document supersedes a verified one", "warning", "change")
def supersedes_verified(ctx: Ctx) -> Iterator[Flag]:
    for e in ctx.expectations:
        for ev in e.events:
            if ev.type == "superseded_after_verification":
                yield Flag(
                    [e.id],
                    f"{e.id}: {ev.text} replaced a verified document; re-review and notify the "
                    "preparer",
                    key=(ev.text or "").split(" ")[-1],
                )


@rule(
    "R-EXP-05", "No successful check recently during filing season", "warning", "missing_evidence"
)
def stale_check(ctx: Ctx) -> Iterator[Flag]:
    if not ctx.param("R-EXP-05", "season_start") <= ctx.today <= ctx.filing_deadline():
        return
    days = ctx.param("R-EXP-05", "days")
    for e in _applicable(ctx):
        if e.acquisition not in ("expected", "available"):
            continue
        check = e.last_check
        ok = (
            check
            and check.result != "could_not_check"
            and (ctx.today - check.at.date()).days <= days
        )
        if not ok:
            last = f"last check {check.at.date()} ({check.result})" if check else "never checked"
            yield Flag([e.id], f"{e.id}: {last}; unknown whether it has been issued")


@rule("R-EXP-06", "Past follow-up date and still expected", "warning", "missing_evidence")
def follow_up_due(ctx: Ctx) -> Iterator[Flag]:
    for e in _applicable(ctx):
        if e.follow_up_at and e.follow_up_at < ctx.today and e.acquisition == "expected":
            yield Flag(
                [e.id],
                f"{e.id}: follow-up was due {e.follow_up_at}; chase the issuer",
            )


@rule("R-EXP-07", "Preparer has a stale version", "warning", "change")
def stale_delivery(ctx: Ctx) -> Iterator[Flag]:
    for e in ctx.expectations:
        if e.delivery == "stale":
            yield Flag([e.id], f"{e.id}: a newer version exists than the one sent to the preparer")
