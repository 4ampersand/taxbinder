"""An invented household for tests that need a vault with a realistic year in it.

The Quills (tests/samples/household.py) and their 2025: partnerships, brokerage statements, a
W-2, rows not received or not issued, donations, estimates, foreign accounts and payments, built
directly as records. Every name, number and amount is made up; account numbers use the 9900
prefix no institution here uses. `build(store)` writes it all as one change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from taxbinder.models import CLAIM_ADAPTER, Institution, Source, Taxpayer
from taxbinder.models.expectation import Expectation
from taxbinder.models.year import YearConfig
from taxbinder.store import RecordStore, keys_from_env, open_vault

YEAR = 2025
KETTLE = "2025/fernhollow-properties.kettle-410.k1"

TAXPAYERS = [
    Taxpayer(id="primary", name="Avery Quill", kind="individual"),
    Taxpayer(id="spouse", name="Jordan Quill", kind="individual"),
    Taxpayer(id="grantor-trust", name="Quill Family Trust", kind="trust"),
]

INSTITUTIONS = [
    ("fernhollow-properties", "Fernhollow properties"),  # as the sponsor writes it
    ("copperfield-collective", "Copperfield Collective"),
    ("corbel-net-lease", "Corbel Net Lease"),
    ("plover-advisors", "Plover Advisors"),
    ("tidemark", "Tidemark"),
    ("brackley-wealth", "Brackley Wealth"),
    ("irs", "IRS"),
    ("willowbend-hsa", "Willowbend HSA Bank"),
    ("linden-education", "Linden Education"),
    ("wexcombe-529", "Wexcombe College Savings Plan"),
    ("social-security", "Social Security Administration"),
    ("lrb", "Lindqvist Regional Bank"),
]

# (source id, name, owner, doc type, row suffix, extra expectation fields, identifiers)
ROWS: list[tuple[str, str, str, str, str, dict[str, Any], list[dict[str, Any]]]] = [
    (
        "fernhollow-properties.kettle-410",
        "Kettle 410",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.cirrus",
        "Cirrus",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.aster-apartments-easton",
        "Aster Apartments(Easton)",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.ridge-at-cobble-flats",
        "Ridge at Cobble Flats(Easton)",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.willow-court",
        "Willow Court",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.cadence-row",
        "Cadence Row",
        "primary",
        "k1_1065",
        "k1",
        {
            "applicability": "deferred_next_year",
            "applicability_note": "Next year (confirm)",
            "acquisition": "vaulted",
            "delivery": "sent",
            "comment": "Next year",
        },
        [],
    ),
    (
        "corbel-net-lease.hawksmoor-office-park-hop",
        "Hawksmoor Office Park(HOP)",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "corbel-net-lease.opportunity-fund-i",
        "Opportunity Fund I",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "corbel-net-lease.tqa",
        "TQA",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.lattice",
        "Lattice",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "fernhollow-properties.bayside-commons",
        "Bayside Commons",
        "primary",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "copperfield-collective.corvina-ledger-fund",
        "Corvina Ledger Fund",
        "primary",
        "k1_1065",
        "k1",
        {
            "acquisition": "received",
            "events": [{"at": "2025-09-03", "type": "received", "text": "date as entered"}],
        },
        [{"number": "9900-4417-21-O", "kind": "reference", "issuer": "copperfield-collective"}],
    ),
    (
        "copperfield-collective.pikeway-inv-partners",
        "Pikeway Inv Partners",
        "primary",
        "k1_1065",
        "k1",
        {},
        [],
    ),
    (
        "copperfield-collective.ashcombe-partners",
        "Ashcombe Partners",
        "primary",
        "k1_1065",
        "k1",
        {"events": [{"at": "2025-09-15", "type": "received", "text": "date as entered"}]},
        [],
    ),
    (
        "plover-advisors.thornbury-capital-fund.individual",
        "Thornbury Capital Fund",
        "primary",
        "k1_1065",
        "k1",
        {},
        [],
    ),
    (
        "plover-advisors.thornbury-capital-fund.trust",
        "Thornbury Capital Fund",
        "grantor-trust",
        "k1_1065",
        "k1",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "tidemark.keystep",
        "Keystep",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [{"number": "9900-5521", "kind": "primary"}],
    ),
    (
        "tidemark.tidemark-stockplan",
        "Tidemark StockPlan",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [{"number": "9900-7705", "kind": "primary"}],
    ),
    (
        "tidemark.tidemark-stockplan-supplemental",
        "Tidemark StockPlan Supplemental",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "received"},
        [],
    ),
    (
        "tidemark.tidemark-premiumsavings",
        "Tidemark PremiumSavings",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [{"number": "9900-258-0006", "kind": "primary"}],
    ),
    (
        "tidemark.tidemark-trust-258-0006",
        "Tidemark Trust 258-0006",
        "grantor-trust",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [{"number": "9900-258-0006", "kind": "primary"}],
    ),  # the same number: a typo to catch
    (
        "brackley-wealth.interest",
        "Interest",
        "primary",
        "1099_int",
        "1099-int",
        {"applicability": "not_issued", "applicability_note": "Not Issued - Old account"},
        [],
    ),
    (
        "brackley-wealth.consolidated-tax-statement",
        "Consolidated Tax Statement",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "brackley-wealth.consolidated-tax-statement-7731",
        "Consolidated Tax Statement",
        "primary",
        "1099_composite",
        "1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [{"number": "9900-31-7731", "kind": "primary"}],
    ),
    (
        "irs.interest-income-from-irs",
        "Interest Income From IRS",
        "primary",
        "1099_int",
        "1099-int",
        {"acquisition": "vaulted", "delivery": "sent", "amount": 412.37},
        [],
    ),
    (
        "willowbend-hsa.hsa",
        "HSA",
        "primary",
        "5498_sa",
        "5498-sa",
        {"acquisition": "vaulted", "delivery": "sent", "amount": 8550.0},
        [],
    ),
    (
        "wexcombe-529.rowan-withdrawal",
        "Rowan Withdrawal",
        "primary",
        "1099_q",
        "1099-q",
        {"applicability": "not_applicable", "applicability_note": "No withdrawal this year"},
        [],
    ),
    (
        "linden-education.jordan",
        "Jordan",
        "spouse",
        "w2",
        "w2",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
    (
        "social-security.benefit-statement",
        "Benefit Statement",
        "primary",
        "ssa_1099",
        "ssa-1099",
        {"acquisition": "vaulted", "delivery": "sent"},
        [],
    ),
]
FOREIGN = [  # FBAR accounts: (source id, owner, highest balance in EUR, interest)
    ("lrb.account-0101", "primary", 14800, 210),
    ("lrb.account-0102", "spouse", 12600, 160),
    ("lrb.account-0103", "spouse", 2400, 30),
]
FEDERAL_PAYMENTS = [  # the first is after the April 15 due date: Q1 is missing
    ("2025-04-18", 9000, "estimated_q2"),
    ("2025-06-13", 9000, "estimated_q2"),
    ("2025-09-12", 9000, "estimated_q3"),
    ("2026-01-14", 9000, "estimated_q4"),
]
STATE_PAYMENTS = [("2025-04-14", 4000, "estimated_q1"), ("2025-06-16", 5000, "estimated_q2")]


def _claim(data: dict[str, Any]) -> Any:
    return CLAIM_ADAPTER.validate_python({"year": YEAR, "provenance": "fixture", **data})


def _hand(value: float, where: str) -> dict[str, Any]:
    return {"value": value, "source": f"fixture:{where}", "kind": "hand_entry"}


def records() -> list[Any]:
    out: list[Any] = [*TAXPAYERS]
    out += [Institution(id=i, name=n) for i, n in INSTITUTIONS]
    for sid, name, owner, doc_type, suffix, fields, ids in ROWS:
        inst = sid.split(".")[0]
        hinted = [{**i, "hint": "…" + i["number"].replace(" ", "")[-4:]} for i in ids]
        out.append(
            Source(id=sid, institution=inst, name=name, owner=owner, identifiers=hinted,
                   produces=[{"doc_type": doc_type}])
        )  # fmt: skip
        out.append(
            Expectation(id=f"{YEAR}/{sid}.{suffix}", year=YEAR, source=sid, doc_type=doc_type,
                        **fields)
        )  # fmt: skip
    for sid, owner, balance, interest in FOREIGN:
        out.append(Source(id=sid, institution="lrb", name=sid.split(".")[1], owner=owner))
        out.append(
            _claim({"id": f"{YEAR}/fbar/{sid}", "category": "fbar", "taxpayer": owner,
                    "assertions": [_hand(balance, f"fbar:{sid}")],
                    "foreign_account": {"source": sid, "currency": "EUR", "max_balance": balance,
                                        "interest": interest, "rate": 0.9}})
        )  # fmt: skip
    out += [
        YearConfig(
            year=YEAR,
            extension_filed=True,
            resident_states=["CA"],
            returns=[
                {"jurisdiction": "federal", "status": "with_preparer", "extended": True},
                {"jurisdiction": "CA", "status": "with_preparer", "extended": True},
            ],
            summaries=[
                {"subject": f"{YEAR}/irs.interest-income-from-irs.1099-int", "value": 412.37,
                 "source": "fixture:summary"},
                {"subject": "tax_payment/federal", "value": 32000, "source": "fixture:summary"},
                {"subject": "tax_payment/CA", "value": 9000, "source": "fixture:summary"},
                {"subject": f"{YEAR}/linden-education.jordan.w2", "value": 61250,
                 "source": "fixture:summary"},
            ],  # fmt: skip
        ),
        _claim({"id": f"{YEAR}/donations/2025-02-26.friends-of-the-marsh-wrens",
                "category": "donation", "date": "2025-02-26", "amount": 2000,
                "taxpayer": "primary", "payee": "Friends of the Marsh Wrens",
                "assertions": [{**_hand(2000, "donations"), "kind": "hand_summary"},
                               _hand(2000, "donations")],
                "donation": {"type": "cash"}}),
        _claim({"id": f"{YEAR}/donations/2025-10-04.giftwell", "category": "donation",
                "date": "2025-10-04", "amount": 12511.20, "taxpayer": "primary",
                "payee": "GiftWell",
                "assertions": [{**_hand(12380.40, "summary"), "kind": "hand_summary"},
                               _hand(12511.20, "donations")],
                "donation": {"type": "securities", "shares": 120, "cost_basis_per_share": 38.5,
                             "acquired": "2019-05-14", "to_daf": True}}),
        _claim({"id": f"{YEAR}/estimates/cap-gains.keystep", "category": "estimate",
                "source": "tidemark.keystep", "short_term": 1840, "long_term": 22650,
                "dividends_interest": 3120, "reconcile_against": "1099_b"}),
        _claim({"id": f"{YEAR}/estimates/cap-gains.thornbury", "category": "estimate",
                "source": "plover-advisors.thornbury-capital-fund.individual",
                "long_term": 9400, "reconcile_against": "k1"}),
        _claim({"id": f"{YEAR}/vehicle-fees/2025-05-29.sedan", "category": "vehicle_fee",
                "date": "2025-05-29", "amount": 186, "taxpayer": "primary",
                "assertions": [_hand(186, "vehicle")], "vehicle": {"description": "Sedan"}}),
        _claim({"id": f"{YEAR}/property-tax/2025-03-01.primary-residence",
                "category": "property_tax", "date": "2025-03-01", "amount": 6120.55,
                "taxpayer": "primary", "assertions": [_hand(6120.55, "property tax")],
                "parcel": {}}),
    ]  # fmt: skip
    for day, amount, kind in FEDERAL_PAYMENTS:
        out.append(_payment("fed", "federal", day, amount, kind))
    for day, amount, kind in STATE_PAYMENTS:
        out.append(_payment("ca", "CA", day, amount, kind))
    return out


def _payment(prefix: str, jurisdiction: str, day: str, amount: float, kind: str) -> Any:
    return _claim(
        {"id": f"{YEAR}/payments/{prefix}.{day}", "category": "tax_payment", "date": day,
         "amount": amount, "taxpayer": "primary", "assertions": [_hand(amount, "payments")],
         "jurisdiction": jurisdiction, "payment": {"kind": kind, "applied_to_year": YEAR}}
    )  # fmt: skip


def build(store: RecordStore, actor: str = "test") -> None:
    with store.operation(actor, "test household"):
        for record in records():
            store.put(record, None)


def build_at(path: Path) -> None:
    """The household into the vault at `path` (made with `taxbinder init`), as the CLI tests do."""
    with RecordStore(open_vault(path, keys_from_env())) as store:
        build(store)


if __name__ == "__main__":  # uv run python -m tests.quills: into $TAXBINDER_VAULT (after init)
    import sys

    from taxbinder.store.vault import REAL_VAULT, default_vault_path

    target = default_vault_path()
    if target == REAL_VAULT:
        sys.exit("set TAXBINDER_VAULT to a throwaway vault first; this never writes the real one")
    build_at(target)
    print(f"added the Quill household's {YEAR} to {target}")
