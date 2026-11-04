"""Reconcile the test household (§2, §16) and finding lifecycle."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from helpers import new_vault
from quills import build

from taxbinder.rules.reconcile import acknowledge, reconcile
from taxbinder.store import RecordStore, fsck

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path):
    with RecordStore(new_vault(tmp_path / "vault")) as s:
        build(s, "test")
        yield s


def ids(result) -> set[str]:  # noqa: ANN001
    return {f.id for f in result.findings.findings if f.status != "resolved"}


def test_acceptance_reproduces_section_2(store: RecordStore) -> None:
    """Every finding in §2 comes out of reconciling the test household."""
    found = ids(reconcile(store, 2025, "test", now=NOW))
    daf = "2025/donations/2025-10-04.giftwell"
    expected = {
        # §2.1 federal estimated payments: checklist 32k vs 36k paid
        "R-PAY-01:2025:federal",
        # §2.1 DAF amount disagrees between sheets
        f"R-DON-10:{daf}",
        # §2.1 duplicate account number on PremiumSavings and Trust
        "R-SRC-02:tidemark.tidemark-premiumsavings:tidemark.tidemark-trust-258-0006",
        # §2.2 Corvina / Ashcombe K-1 dates in 2025
        "R-CON-02:2025/copperfield-collective.corvina-ledger-fund.k1:2025-09-03",
        "R-CON-02:2025/copperfield-collective.ashcombe-partners.k1:2025-09-15",
        # §2.2 received, not vaulted
        "R-EXP-02:2025/tidemark.tidemark-stockplan-supplemental.1099",
        "R-EXP-02:2025/copperfield-collective.corvina-ledger-fund.k1",
        # §2.3 the Marsh Wrens gift needs an acknowledgment
        "R-DON-01:2025/donations/2025-02-26.friends-of-the-marsh-wrens:written_acknowledgment",
        # §2.3 DAF needs the sponsor's acknowledgment and Form 8283 Section A
        f"R-DON-03:{daf}:written_acknowledgment_daf",
        f"R-DON-02:{daf}:form_8283_section_a",
        # FBAR required (both owners over $10,000)
        "R-FOR-01:primary",
        "R-FOR-01:spouse",
        # §2.4 timing: federal Q1 installment missing (first payment 4/18)
        "R-PAY-03:2025:federal.q1",
    }
    assert expected <= found, sorted(expected - found)


def test_requirements_written_to_claims(store: RecordStore) -> None:
    reconcile(store, 2025, "test", now=NOW)
    gift, _ = store.get("claim", "2025/donations/2025-02-26.friends-of-the-marsh-wrens")
    assert [(q.id, q.rule, q.accepts) for q in gift.requirements] == [
        ("written_acknowledgment", "R-DON-01", ["donation_ack"])
    ]
    assert gift.status == "evidence_requested"
    estimate, _ = store.get("claim", "2025/estimates/cap-gains.keystep")
    assert estimate.requirements == [] and estimate.status == "entered"


def test_rerun_is_stable(store: RecordStore) -> None:
    first = reconcile(store, 2025, "test", now=NOW)
    later = NOW + timedelta(hours=1)
    second = reconcile(store, 2025, "test", now=later, today=NOW.date())
    assert second.claims_updated == 0
    assert ids(first) == ids(second)
    f = next(f for f in second.findings.findings if f.id == "R-PAY-01:2025:federal")
    assert (f.first_seen, f.last_seen) == (NOW, later)
    assert fsck(store.vault) == []


def test_acknowledge_then_change_reopens(store: RecordStore) -> None:
    reconcile(store, 2025, "test", now=NOW)
    fid = "R-PAY-01:2025:federal"
    with pytest.raises(ValueError):
        acknowledge(store, 2025, fid, " ", "avery")
    acknowledge(store, 2025, fid, "CPA confirmed 36k is right", "avery")

    again = reconcile(store, 2025, "test", now=NOW)
    f = next(f for f in again.findings.findings if f.id == fid)
    assert f.status == "acknowledged" and f.resolution_note == "CPA confirmed 36k is right"

    # An extra payment changes the message → the finding reopens.
    pay, _ = store.get("claim", "2025/payments/fed.2025-04-18")
    extra = pay.model_copy(update={"id": "2025/payments/fed.2025-04-19", "date": date(2025, 4, 19)})
    with store.operation("avery", "add payment"):
        store.put(extra, None)
    reopened = reconcile(store, 2025, "test", now=NOW)
    f = next(f for f in reopened.findings.findings if f.id == fid)
    assert f.status == "open" and "5 payments" in f.message


def test_fixed_problem_resolves(store: RecordStore) -> None:
    reconcile(store, 2025, "test", now=NOW)
    daf_id = "2025/donations/2025-10-04.giftwell"
    daf, rev = store.get("claim", daf_id)
    fixed = type(daf).model_validate(
        {
            **daf.model_dump(),
            "resolved": {"value": 12511.20, "by": "avery", "note": "DAF letter", "at": NOW.date()},
        }
    )
    with store.operation("avery", "resolve DAF amount"):
        store.put(fixed, rev)
    result = reconcile(store, 2025, "test", now=NOW)
    f = next(f for f in result.findings.findings if f.id == f"R-DON-10:{daf_id}")
    assert f.status == "resolved" and "no longer raised" in f.resolution_note
