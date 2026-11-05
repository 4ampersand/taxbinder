"""Rule catalog (§8.2): each rule on small synthetic records."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from taxbinder.models import CLAIM_ADAPTER, Document, Expectation, Institution, Source, YearConfig
from taxbinder.models.year import TrackedReturn
from taxbinder.rules.engine import RULES, Ctx, load_catalog, run
from taxbinder.rules.params import load_params

load_catalog()
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
TODAY = NOW.date()


def ctx(**kw: Any) -> Ctx:
    base: dict[str, Any] = dict(
        year=2025,
        params=load_params(2025),
        today=TODAY,
        year_config=YearConfig(year=2025, extension_filed=True, resident_states=["CA"]),
        institutions=[],
        sources=[],
        expectations=[],
        prior_expectations=[],
        documents=[],
        claims=[],
    )
    base.update(kw)
    return Ctx(**base)


def found(c: Ctx, rule_id: str) -> list:
    return [f for f in run(c, NOW, only=[rule_id]).findings if f.rule == rule_id]


def exp(source: str = "fernhollow-properties.kettle-410", **kw: Any) -> Expectation:
    return Expectation(id=f"2025/{source}.k1", year=2025, source=source, doc_type="k1_1065", **kw)


def src(id: str, *numbers: str, **kw: Any) -> Source:
    inst = id.split(".")[0]
    return Source(
        id=id,
        institution=inst,
        name=id,
        owner="primary",
        identifiers=[{"number": n, "hint": "…" + n[-4:], **kw} for n in numbers],
    )


def claim(data: dict[str, Any]) -> Any:
    return CLAIM_ADAPTER.validate_python({"year": 2025, **data})


def donation(slug: str, amount: float, type_: str = "cash", **kw: Any) -> Any:
    extra = kw.pop("donation", {})
    return claim(
        {
            "id": f"2025/donations/2025-02-26.{slug}",
            "category": "donation",
            "date": kw.pop("date", "2025-02-26"),
            "amount": amount,
            "payee": kw.pop("payee", slug),
            "donation": {"type": type_, **extra},
            **kw,
        }
    )


def payment(j: str, day: str, amount: float, kind: str = "estimated_q1", **kw: Any) -> Any:
    return claim(
        {
            "id": f"2025/payments/{j.lower()}.{day}",
            "category": "tax_payment",
            "jurisdiction": j,
            "date": day,
            "amount": amount,
            "payment": {"kind": kind, "applied_to_year": 2025},
            **kw,
        }
    )


def doc(n: int, doc_type: str, tax_year: int = 2025, **kw: Any) -> Document:
    sha = f"{n:012x}" + "0" * 52
    return Document(
        id=f"doc_{sha[:12]}",
        sha256=sha,
        blob=f"{sha}.pdf",
        original_filename=f"doc{n}.pdf",
        mime="application/pdf",
        bytes=1,
        acquired={"at": kw.pop("acquired_at", NOW), "via": "upload"},
        classification={"doc_type": doc_type, "tax_year": tax_year, **kw},
    )


def with_(record: Any, **update: Any) -> Any:
    """Copy with changes, validated (model_copy(update=...) would skip validation)."""
    return type(record).model_validate({**record.model_dump(), **update})


def link(d: Document) -> dict[str, Any]:
    return {"doc": d.id, "linked_at": TODAY, "linked_by": "test"}


def test_catalog_complete() -> None:
    expected = (
        [f"R-SRC-0{i}" for i in range(1, 7)]
        + [f"R-EXP-0{i}" for i in range(1, 8)]
        + [f"R-DON-{i:02d}" for i in range(1, 11)]
        + [f"R-PAY-0{i}" for i in range(1, 6)]
        + [f"R-CON-0{i}" for i in range(1, 4)]
        + ["R-FOR-01"]
        + ["R-BIL-01", "R-BIL-02"]
        + ["R-RET-01"]
    )
    assert sorted(RULES) == sorted(expected)


# -- sources -------------------------------------------------------------------------------


def test_src_01_prior_year_gap() -> None:
    prior = Expectation(
        id="2024/fernhollow-properties.kettle-410.k1",
        year=2024,
        source="fernhollow-properties.kettle-410",
        doc_type="k1_1065",
    )
    assert len(found(ctx(prior_expectations=[prior]), "R-SRC-01")) == 1
    assert found(ctx(prior_expectations=[prior], expectations=[exp()]), "R-SRC-01") == []


def test_src_02_duplicate_identifier_and_dates() -> None:
    a, b = src("tidemark.savings", "123456789"), src("tidemark.trust", "123-456-789")
    [f] = found(ctx(sources=[a, b]), "R-SRC-02")
    assert f.subjects == ["tidemark.savings", "tidemark.trust"] and "123456789" not in f.message
    old = src("tidemark.old", "123456789", effective_to=date(2020, 1, 1))
    new = src("tidemark.new", "123456789", effective_from=date(2021, 1, 1))
    assert found(ctx(sources=[old, new]), "R-SRC-02") == []


def test_src_03_and_04_unmatched_documents() -> None:
    sources = [src("tidemark.trust", "123456789")]
    unknown_issuer = doc(1, "1099_composite", issuer="new-fund")
    unknown_account = doc(2, "1099_composite", issuer="tidemark", account_number="XXXX9999")
    known_account = doc(
        3, "1099_composite", issuer="tidemark", account_number="XXXX6789", source="tidemark.trust"
    )
    c = ctx(sources=sources, documents=[unknown_issuer, unknown_account, known_account])
    assert [f.subjects for f in found(c, "R-SRC-03")] == [[unknown_issuer.id]]
    [f] = found(c, "R-SRC-04")
    assert f.subjects == [unknown_account.id] and "…9999" in f.message
    assert "tidemark.trust" in f.suggested_action
    ambiguous = doc(4, "1099_composite", issuer="tidemark")
    [g] = found(ctx(sources=sources, documents=[ambiguous]), "R-SRC-04")
    assert "could not be matched" in g.message


def test_src_05_split_reporting() -> None:
    s = src("tidemark.trust", "111111111", effective_to=date(2025, 5, 15))
    assert len(found(ctx(sources=[s], expectations=[exp("tidemark.trust")]), "R-SRC-05")) == 1


def test_src_06_lineage() -> None:
    inst = Institution(
        id="tidemark",
        name="TideMark",
        lineage=[
            {"relation": "acquired_by", "institution": "brackley-wealth", "effective": "2026-05-16"}
        ],
    )
    assert len(found(ctx(institutions=[inst]), "R-SRC-06")) == 1


# -- expectations --------------------------------------------------------------------------


def test_exp_01_deadline_window() -> None:
    e = exp()
    assert len(found(ctx(expectations=[e]), "R-EXP-01")) == 1  # 16 days to Oct 15
    assert found(ctx(expectations=[e], today=date(2026, 9, 1)), "R-EXP-01") == []
    verified = exp(review="verified")
    assert found(ctx(expectations=[verified]), "R-EXP-01") == []


def test_exp_02_received_not_vaulted() -> None:
    fresh = exp(acquisition="received", events=[{"at": TODAY, "type": "received"}])
    old = exp(
        "tidemark.trust",
        acquisition="received",
        events=[{"at": date(2026, 9, 1), "type": "received"}],
    )
    assert [f.subjects[0] for f in found(ctx(expectations=[fresh, old]), "R-EXP-02")] == [old.id]


def test_exp_03_other_without_note_is_rejected_by_model() -> None:
    with pytest.raises(ValueError):
        exp(applicability="other")
    assert (
        found(ctx(expectations=[exp(applicability="other", applicability_note="x")]), "R-EXP-03")
        == []
    )


def test_exp_04_supersedes_verified() -> None:
    e = exp(
        events=[
            {"at": TODAY, "type": "superseded", "text": "doc_000000000001 → doc_000000000002"},
            {
                "at": TODAY,
                "type": "superseded_after_verification",
                "text": "doc_000000000002 → doc_000000000003",
            },
        ]
    )
    [f] = found(ctx(expectations=[e]), "R-EXP-04")
    assert f.id.endswith("doc_000000000003")


def test_exp_05_no_recent_check() -> None:
    never = exp()
    ok = exp(
        "tidemark.trust",
        last_check={"at": NOW, "via": "portal_window", "result": "not_found"},
    )
    failed = exp(
        "wf.mortgage",
        last_check={"at": NOW, "via": "portal_window", "result": "could_not_check"},
    )
    ids = [f.subjects[0] for f in found(ctx(expectations=[never, ok, failed]), "R-EXP-05")]
    assert ids == [never.id, failed.id]


def test_exp_06_and_07() -> None:
    e = exp(follow_up_at=date(2026, 9, 1))
    assert len(found(ctx(expectations=[e]), "R-EXP-06")) == 1
    assert len(found(ctx(expectations=[exp(delivery="stale")]), "R-EXP-07")) == 1


# -- donations -----------------------------------------------------------------------------


def test_don_01_threshold_and_satisfaction() -> None:
    small, big = donation("small", 249), donation("big", 250)
    assert [f.subjects[0] for f in found(ctx(claims=[small, big]), "R-DON-01")] == [big.id]
    ack = doc(1, "donation_ack")
    linked = with_(big, evidence=[link(ack)])
    result = run(ctx(claims=[linked], documents=[ack]), NOW, only=["R-DON-01"])
    assert result.findings == []
    assert result.requirements[big.id][0].satisfied_by == ack.id
    wrong_year = doc(2, "donation_ack", tax_year=2024)
    linked = with_(big, evidence=[link(wrong_year)])
    assert len(found(ctx(claims=[linked], documents=[wrong_year]), "R-DON-01")) == 1
    january = doc(3, "donation_ack", tax_year=2026)  # the letter came the next January
    by_person = with_(big, evidence=[link(january)])
    assert found(ctx(claims=[by_person], documents=[january]), "R-DON-01") == []
    by_matcher = with_(big, evidence=[{**link(january), "linked_by": "auto:matcher"}])
    assert len(found(ctx(claims=[by_matcher], documents=[january]), "R-DON-01")) == 1


def test_don_01_waiver_satisfies() -> None:
    waived = donation(
        "big",
        500,
        requirements=[
            {
                "id": "written_acknowledgment",
                "rule": "R-DON-01",
                "waived": {
                    "reason": "other",
                    "note": "church letter lost",
                    "by": "me",
                    "at": TODAY,
                },
            }
        ],
    )
    assert found(ctx(claims=[waived]), "R-DON-01") == []


def test_don_02_03_05_securities_to_daf() -> None:
    daf = donation("daf", 12511.20, "securities", donation={"to_daf": True, "shares": 120})
    c = ctx(claims=[daf])
    assert [f.id.rsplit(":", 1)[1] for f in found(c, "R-DON-02")] == ["form_8283_section_a"]
    assert len(found(c, "R-DON-03")) == 1
    assert "120 shares" in found(c, "R-DON-05")[0].message
    prop = donation("art", 6000, "property")
    assert len(found(ctx(claims=[prop]), "R-DON-02")) == 2  # Section A + appraisal


def test_don_04_and_10_assertions() -> None:
    two = donation(
        "daf",
        12511.20,
        assertions=[
            {"value": 12380.40, "source": "hand:summary", "kind": "hand_summary"},
            {"value": 12511.20, "source": "hand:gifts", "kind": "hand_entry"},
        ],
    )
    assert len(found(ctx(claims=[two]), "R-DON-10")) == 1
    resolved = with_(
        two, resolved={"value": 12511.20, "by": "me", "note": "DAF letter", "at": TODAY}
    )
    assert found(ctx(claims=[resolved]), "R-DON-10") == []
    doc_says = donation(
        "x", 100, assertions=[{"value": 90, "source": "doc_000000000001", "kind": "document"}]
    )
    assert len(found(ctx(claims=[doc_says]), "R-DON-04")) == 1


def test_don_06_short_term_securities() -> None:
    short = donation(
        "st",
        5000,
        "securities",
        donation={"acquired": "2024-12-01", "shares": 10, "cost_basis_per_share": 100},
    )
    assert "limited to basis $1,000.00" in found(ctx(claims=[short]), "R-DON-06")[0].message


def test_don_07_08_09() -> None:
    gala = donation("gala", 500, donation={"quid_pro_quo": True})
    assert len(found(ctx(claims=[gala]), "R-DON-07")) == 1

    late = doc(1, "donation_ack", acquired_at=datetime(2026, 11, 1, tzinfo=UTC))
    gift = donation("x", 300, evidence=[link(late)])
    assert (
        "not contemporaneous" in found(ctx(claims=[gift], documents=[late]), "R-DON-08")[0].message
    )

    stmt = doc(2, "donation_ack")
    a = donation("church", 300, payee="Church", evidence=[link(stmt)])
    b = donation("church-2", 400, payee="Church", evidence=[link(stmt)], date="2025-03-01")
    assert len(found(ctx(claims=[a, b], documents=[stmt]), "R-DON-09")) == 1


# -- payments ------------------------------------------------------------------------------


FED = [
    payment("federal", "2025-04-18", 40000, "estimated_q2"),
    payment("federal", "2025-08-19", 96000, "estimated_q3"),
    payment("federal", "2025-11-02", 40000, "estimated_q4"),
    payment("federal", "2026-03-31", 64000, "extension"),
]


def year_with(*summaries: tuple[str, float]) -> YearConfig:
    return YearConfig(
        year=2025,
        extension_filed=True,
        resident_states=["CA"],
        summaries=[{"subject": s, "value": v, "source": "hand:summary"} for s, v in summaries],
    )


def test_pay_01_summary_mismatch() -> None:
    [f] = found(ctx(claims=FED, year_config=year_with(("tax_payment/federal", 200000))), "R-PAY-01")
    assert "$240,000.00" in f.message and "$200,000.00" in f.message
    assert (
        found(ctx(claims=FED, year_config=year_with(("tax_payment/federal", 240000))), "R-PAY-01")
        == []
    )


def test_pay_02_confirmation_required() -> None:
    assert len(found(ctx(claims=FED), "R-PAY-02")) == 4


def test_pay_03_missing_installment_and_kind() -> None:
    ids = [f.id for f in found(ctx(claims=FED), "R-PAY-03")]
    assert ids == ["R-PAY-03:2025:federal.q1"]
    wrong = payment("CA", "2025-08-18", 40000, "estimated_q3")
    kinds = [f for f in found(ctx(claims=[wrong]), "R-PAY-03") if f.id.endswith(":kind")]
    assert "estimated_q4" in kinds[0].message


def test_pay_04_05_state() -> None:
    il = payment("IL", "2025-10-31", 2000, notes="Paid $180 in penalties and interest for 2024")
    ca = payment("CA", "2025-04-11", 5000)
    assert [f.id for f in found(ctx(claims=[il, ca]), "R-PAY-04")] == [f"R-PAY-04:{il.id}:IL"]
    assert len(found(ctx(claims=[il]), "R-PAY-05")) == 1


# -- consistency and foreign ---------------------------------------------------------------


def test_con_01_02_03() -> None:
    fbar = claim(
        {
            "id": "2025/fbar/lrb.a-0012",
            "category": "fbar",
            "assertions": [
                {"value": 1, "source": "a", "kind": "hand_entry"},
                {"value": 5, "source": "b", "kind": "hand_summary"},
            ],
            "foreign_account": {"source": "lrb.a-0012", "currency": "EUR", "max_balance": 1},
        }
    )
    assert len(found(ctx(claims=[fbar]), "R-CON-01")) == 1

    typo = exp(events=[{"at": date(2025, 9, 3), "type": "received"}])
    fine = exp("tidemark.trust", events=[{"at": date(2026, 3, 1), "type": "received"}])
    assert [f.subjects[0] for f in found(ctx(expectations=[typo, fine]), "R-CON-02")] == [typo.id]

    a = donation("x", 100, payee="Food Bank")
    b = donation("y", 100, payee="food bank ", date="2025-02-28")
    assert len(found(ctx(claims=[a, b]), "R-CON-03")) == 1


def test_for_01_per_owner_and_filing_evidence() -> None:
    def fbar(n: int, owner: str, eur: float) -> Any:
        return claim(
            {
                "id": f"2025/fbar/lrb.a-{n:04d}",
                "category": "fbar",
                "taxpayer": owner,
                "foreign_account": {
                    "source": f"lrb.a-{n:04d}",
                    "currency": "EUR",
                    "max_balance": eur,
                    "rate": 0.9,
                },
            }
        )

    claims = [fbar(1, "primary", 10_000), fbar(2, "spouse", 4_500)]
    [f] = found(ctx(claims=claims), "R-FOR-01")
    assert f.subjects[0] == "primary" and "$11,111" in f.message
    filed = doc(1, "fbar_filing")
    assert found(ctx(claims=claims, documents=[filed]), "R-FOR-01") == []
    # a joint account counts in full for each owner: €5,000 joint puts the spouse over too
    joint = [*claims, fbar(3, None, 5_000)]
    flagged = sorted(f.subjects[0] for f in found(ctx(claims=joint), "R-FOR-01"))
    assert flagged == ["primary", "spouse"]
    [f] = found(ctx(claims=[fbar(4, None, 9_500)]), "R-FOR-01")  # joint only: still over
    assert f.subjects[0] == "unknown"


def test_findings_carry_params_hash_and_version() -> None:
    [f] = found(ctx(claims=[donation("big", 300)]), "R-DON-01")
    assert f.params_hash == load_params(2025).hash and f.rule_version == "1"
    assert f.classification == "missing_evidence" and f.severity == "error"


def test_a_federal_return_not_filed_near_its_deadline() -> None:
    near = date(2026, 10, 1)  # two weeks before the extended deadline
    [f] = found(ctx(today=near), "R-RET-01")
    assert f.id == "R-RET-01:2025:federal" and "14 days to the 2026-10-15 deadline" in f.message
    assert not found(ctx(today=date(2026, 6, 1)), "R-RET-01")  # not near yet
    filed = YearConfig(
        year=2025, extension_filed=True, returns=[TrackedReturn(status="filed")]
    )
    assert not found(ctx(today=near, year_config=filed), "R-RET-01")
