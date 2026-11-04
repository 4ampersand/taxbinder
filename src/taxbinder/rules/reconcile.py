"""Reconcile a tax year: run the rules against the vault and store the outcome (§8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from taxbinder.entities import entity_info
from taxbinder.models import Finding, FindingSet, YearConfig
from taxbinder.rules.engine import Ctx, Result, load_catalog, run
from taxbinder.rules.params import load_params
from taxbinder.store import RecordNotFound, RecordStore


def load_ctx(store: RecordStore, year: int, today: date) -> Ctx:
    load_catalog()
    claims = store.list("claim", year)
    try:
        year_config, _ = store.get("year", str(year))
    except RecordNotFound:
        year_config = YearConfig(year=year)
    return Ctx(
        year=year,
        params=load_params(year, entity_info(store).form),
        today=today,
        year_config=year_config,
        institutions=store.list("institution"),
        sources=store.list("source"),
        expectations=store.list("expectation", year),
        prior_expectations=store.list("expectation", year - 1),
        documents=store.list("document"),
        claims=[c for c in claims if not c.unpaid],
        planned=[c for c in claims if c.unpaid],
    )


def merge(previous: list[Finding], current: list[Finding], now: datetime) -> list[Finding]:
    """Carry status across runs.

    - first_seen is kept from the first run that raised the finding;
    - an acknowledged finding stays acknowledged while its message is unchanged (its inputs
      haven't changed); a changed message reopens it;
    - a finding that is no longer raised becomes resolved.
    """
    old = {f.id: f for f in previous}
    merged = []
    for f in current:
        before = old.pop(f.id, None)
        if before is None:
            merged.append(f)
            continue
        keep_ack = before.status == "acknowledged" and before.message == f.message
        merged.append(
            f.model_copy(
                update={
                    "first_seen": before.first_seen,
                    "status": "acknowledged" if keep_ack else "open",
                    "resolution_note": before.resolution_note if keep_ack else None,
                }
            )
        )
    for f in old.values():
        if f.status != "resolved":
            f = f.model_copy(
                update={
                    "status": "resolved",
                    "resolution_note": f"no longer raised on {now.date()}",
                }
            )
        merged.append(f)
    return sorted(merged, key=lambda f: f.id)


def _claim_status(claim, requirements) -> str:  # noqa: ANN001
    if claim.status not in ("entered", "evidence_requested", "evidenced") or not requirements:
        return claim.status
    done = all(q.satisfied_by or q.waived for q in requirements)
    return "evidenced" if done else "evidence_requested"


@dataclass
class ReconcileResult:
    findings: FindingSet
    claims_updated: int
    result: Result


def reconcile(
    store: RecordStore,
    year: int,
    actor: str,
    today: date | None = None,
    now: datetime | None = None,
) -> ReconcileResult:
    now = now or datetime.now(UTC)
    from taxbinder.review import (
        adopt_linked_types,  # files people attached count as what they're for
    )

    adopt_linked_types(store, year, actor)
    ctx = load_ctx(store, year, today or now.date())
    result = run(ctx, now)

    try:
        previous, rev = store.get("findings", str(year))
    except RecordNotFound:
        previous, rev = None, None
    findings = FindingSet(
        year=year,
        ran_at=now,
        params_hash=ctx.params.hash,
        findings=merge(previous.findings if previous else [], result.findings, now),
    )

    updated = 0
    with store.operation(actor, f"reconcile {year}"):
        for claim in ctx.claims:
            reqs = result.requirements.get(claim.id, [])
            status = _claim_status(claim, reqs)
            if reqs != claim.requirements or status != claim.status:
                _, claim_rev = store.get("claim", claim.id)
                store.put(
                    claim.model_copy(update={"requirements": reqs, "status": status}), claim_rev
                )
                updated += 1
        store.put(findings, rev)
    return ReconcileResult(findings, updated, result)


def acknowledge(store: RecordStore, year: int, finding_id: str, note: str, actor: str) -> Finding:
    """Mark a finding acknowledged; it stays quiet until its message changes."""
    if not note.strip():
        raise ValueError("acknowledging a finding needs a note")
    findings, rev = store.get("findings", str(year))
    target = next((f for f in findings.findings if f.id == finding_id), None)
    if target is None:
        raise KeyError(f"no finding {finding_id!r} in {year}")
    updated = target.model_copy(update={"status": "acknowledged", "resolution_note": note})
    new = findings.model_copy(
        update={"findings": [updated if f.id == finding_id else f for f in findings.findings]}
    )
    with store.operation(actor, f"acknowledge {finding_id}"):
        store.put(new, rev)
    return updated
