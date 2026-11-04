"""Validation rejects the bad cases."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from taxbinder.models import CLAIM_ADAPTER, Document, Expectation, Institution, Source
from taxbinder.models.claim import Resolution
from taxbinder.models.registry import Identifier

EXPECTATION = {
    "id": "2025/fernhollow-properties.kettle-410.k1",
    "year": 2025,
    "source": "fernhollow-properties.kettle-410",
    "doc_type": "k1_1065",
}
DOCUMENT = {
    "id": "doc_ab12cd34ef56",
    "sha256": "ab12cd34ef56" + "0" * 52,
    "blob": "ab/12/x.pdf.age",
    "original_filename": "x.pdf",
    "mime": "application/pdf",
    "bytes": 1,
    "acquired": {"at": "2026-03-14T09:12:00-07:00", "via": "upload"},
}
DONATION = {
    "id": "2025/donations/2025-02-26.friends-of-the-marsh-wrens",
    "year": 2025,
    "category": "donation",
    "date": "2025-02-26",
    "amount": 2000,
    "payee": "Friends of the Marsh Wrens",
    "donation": {"type": "cash"},
}


def test_valid_baselines() -> None:
    Expectation.model_validate(EXPECTATION)
    Document.model_validate(DOCUMENT)
    CLAIM_ADAPTER.validate_python(DONATION)


@pytest.mark.parametrize("note", [None, "", "   "])
def test_not_applicable_requires_note(note: str | None) -> None:
    with pytest.raises(ValidationError, match="requires applicability_note"):
        Expectation.model_validate(
            EXPECTATION | {"applicability": "not_issued", "applicability_note": note}
        )


def test_not_applicable_with_note_ok() -> None:
    e = Expectation.model_validate(
        EXPECTATION | {"applicability": "not_issued", "applicability_note": "Sponsor confirmed"}
    )
    assert e.applicability == "not_issued"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "2024/fernhollow-properties.kettle-410.k1"},  # year mismatch
        {"id": "2025/fernhollow-properties.other.k1"},  # different source
    ],
)
def test_expectation_id_must_match(changes: dict) -> None:
    with pytest.raises(ValidationError, match="expectation id"):
        Expectation.model_validate(EXPECTATION | changes)


def test_source_id_must_start_with_institution() -> None:
    with pytest.raises(ValidationError, match="must start with 'fernhollow-properties.'"):
        Source.model_validate(
            {
                "id": "tidemark.kettle-410",
                "institution": "fernhollow-properties",
                "name": "x",
                "owner": "s",
            }
        )


def test_document_id_must_match_hash() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        Document.model_validate(DOCUMENT | {"id": "doc_000000000000"})


def test_identifier_dates_ordered() -> None:
    with pytest.raises(ValidationError, match="before effective_from"):
        Identifier(number="x", effective_from=date(2026, 1, 1), effective_to=date(2025, 1, 1))


def test_resolution_needs_basis_or_note() -> None:
    with pytest.raises(ValidationError, match="basis"):
        Resolution(value=Decimal("1"), by="avery", at=date(2026, 10, 2))
    Resolution(value=Decimal("1"), by="avery", at=date(2026, 10, 2), note="per CPA call")


def test_claim_id_must_match_year_and_category() -> None:
    with pytest.raises(ValidationError, match="must start with '2025/donations/'"):
        CLAIM_ADAPTER.validate_python(DONATION | {"id": "2025/payments/2025-02-26.x"})
    with pytest.raises(ValidationError, match="must start with '2025/donations/'"):
        CLAIM_ADAPTER.validate_python(DONATION | {"id": "2024/donations/2025-02-26.x"})


def test_donation_requires_amount() -> None:
    with pytest.raises(ValidationError):
        CLAIM_ADAPTER.validate_python({k: v for k, v in DONATION.items() if k != "amount"})


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Expectation.model_validate(EXPECTATION | {"acquisiton": "received"})


def test_unknown_status_value_rejected() -> None:
    with pytest.raises(ValidationError):
        Expectation.model_validate(EXPECTATION | {"acquisition": "uploaded"})


def test_classification_method() -> None:
    ok = DOCUMENT | {"classification": {"method": "rules+human"}}
    Document.model_validate(ok)
    with pytest.raises(ValidationError, match="method"):
        Document.model_validate(DOCUMENT | {"classification": {"method": "rules+guess"}})


def test_connector_fields_are_refused() -> None:
    """A portal holds its address only, and a document doesn't say which institution fetched it;
    older vaults that had these fields lose them in the format-3 step."""
    for extra in ({"portal": {"connector": "x"}}, {"portal": {"credential_ref": "op://a/b"}},
                  {"portal": {"mfa": "email_otp"}}, {"notifications": {"email_from": []}}):
        with pytest.raises(ValidationError, match="Extra inputs"):
            Institution.model_validate({"id": "x", "name": "X"} | extra)
    with pytest.raises(ValidationError, match="Extra inputs"):
        Document.model_validate(DOCUMENT | {"acquired": DOCUMENT["acquired"] | {"from": "x"}})


def test_money_is_decimal() -> None:
    claim = CLAIM_ADAPTER.validate_python(DONATION | {"amount": 12511.20})
    assert claim.amount == Decimal("12511.20")
