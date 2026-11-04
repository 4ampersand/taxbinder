"""Claims: hand-entered or extracted facts with amounts (§3.4, §5.5).

`Claim` is a union discriminated by `category`; validate with `CLAIM_ADAPTER`.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from taxbinder.models.common import (
    ClaimId,
    DocId,
    DocType,
    Link,
    Money,
    Record,
    RuleId,
    Slug,
    SourceId,
    When,
)

CATEGORY_DIRS = {
    "donation": "donations",
    "tax_payment": "payments",
    "estimate": "estimates",
    "property_tax": "property-tax",
    "vehicle_fee": "vehicle-fees",
    "fbar": "fbar",
    "bill": "bills",
}
"""Claim category → directory under `years/<year>/claims/` (and the middle part of its id)."""


class Assertion(Record):
    """One reported value for a claim, with where it came from."""

    value: Money
    source: str
    kind: Literal["hand_summary", "hand_entry", "document", "extraction"]


class Resolution(Record):
    """Which assertion wins. Must cite evidence (`basis`) or explain in `note`."""

    value: Money
    by: str
    basis: str | None = None
    note: str | None = None
    at: When

    @model_validator(mode="after")
    def _cited(self) -> Resolution:
        if not (self.basis or (self.note or "").strip()):
            raise ValueError("resolution needs a basis (evidence) or a note")
        return self


class Waiver(Record):
    reason: str
    note: str
    by: str
    at: When


class Requirement(Record):
    """Evidence a rule demands for a claim; computed by rules, not typed by hand (§8)."""

    id: str
    rule: RuleId
    accepts: list[DocType] = []
    satisfied_by: DocId | None = None
    waived: Waiver | None = None


class PaymentPlan(Record):
    """A payment that hasn't happened yet: due, maybe scheduled with the bank or payee (§5.5).

    Planned and scheduled payments are not payments: the checks that total payments ignore them
    until they are marked paid. TaxBinder never moves money; this is the plan and the reminder.
    """

    due: dt.date
    pay_on: dt.date | None = None  # the date it's scheduled for
    state: Literal["planned", "scheduled", "paid"] = "planned"
    payee: str = ""
    label: str = ""  # e.g. "1st installment"
    note: str = ""  # how it's paid / scheduled, e.g. "Chase bill pay"


class ClaimBase(Record):
    id: ClaimId
    year: int
    category: str
    date: dt.date | None = None
    amount: Money | None = None
    currency: str = "USD"
    taxpayer: Slug | None = None
    entered_by: str | None = None
    entered_at: dt.datetime | None = None
    provenance: str | None = None
    assertions: list[Assertion] = []
    resolved: Resolution | None = None
    requirements: list[Requirement] = []
    evidence: list[Link] = []
    status: Literal["draft", "entered", "evidence_requested", "evidenced", "verified", "waived"] = (
        "entered"
    )
    notes: str | None = None
    plan: PaymentPlan | None = None  # bills and scheduled payments; None = an actual payment

    @property
    def unpaid(self) -> bool:
        return self.plan is not None and self.plan.state != "paid"

    @model_validator(mode="after")
    def _id_matches(self) -> ClaimBase:
        prefix = f"{self.year}/{CATEGORY_DIRS[self.category]}/"
        if not self.id.startswith(prefix):
            raise ValueError(f"claim id {self.id!r} must start with {prefix!r}")
        return self


class DonationDetails(Record):
    type: Literal["cash", "securities", "property", "daf_grant"]
    method: str | None = None
    quid_pro_quo: bool | None = None
    security: str | None = None
    shares: Money | None = None
    cost_basis_per_share: Money | None = None
    acquired: dt.date | None = None
    holding_period: Literal["short_term", "long_term"] | None = None
    fmv_method: Literal["high_low_average", "close"] | None = None
    to_daf: bool | None = None


class DonationClaim(ClaimBase):
    category: Literal["donation"]
    date: dt.date
    amount: Money
    payee: str
    payee_ein: str | None = None
    donation: DonationDetails


class PaymentDetails(Record):
    kind: Literal[
        "estimated_q1", "estimated_q2", "estimated_q3", "estimated_q4", "extension", "balance_due"
    ]
    applied_to_year: int
    channel: str | None = None
    confirmation_number: str | None = None


class PaymentClaim(ClaimBase):
    category: Literal["tax_payment"]
    date: dt.date
    amount: Money
    jurisdiction: str = Field(description="federal | CA | IL | …")
    payment: PaymentDetails


class EstimateClaim(ClaimBase):
    """Projection, not evidence-bearing."""

    category: Literal["estimate"]
    source: SourceId | None = None
    short_term: Money | None = None
    long_term: Money | None = None
    dividends_interest: Money | None = None
    reconcile_against: str | None = None


class Parcel(Record):
    county: str | None = None
    parcel_number: str | None = None
    installment: Literal["first", "second", "full"] | None = None


class PropertyTaxClaim(ClaimBase):
    category: Literal["property_tax"]
    parcel: Parcel


class Vehicle(Record):
    description: str
    vlf: Money | None = Field(None, description="Deductible vehicle license fee portion")


class VehicleFeeClaim(ClaimBase):
    category: Literal["vehicle_fee"]
    vehicle: Vehicle


class ForeignAccount(Record):
    source: SourceId
    currency: str
    max_balance: Money
    interest: Money | None = None
    withholding: Money | None = None
    rate: Money | None = Field(None, description="Year-end Treasury rate, units per USD")
    usd_value: Money | None = None


class FbarClaim(ClaimBase):
    category: Literal["fbar"]
    foreign_account: ForeignAccount


class BillClaim(ClaimBase):
    """Any other bill to pay and keep proof of (insurance premium, HOA …)."""

    category: Literal["bill"]


Claim = Annotated[
    DonationClaim
    | PaymentClaim
    | EstimateClaim
    | PropertyTaxClaim
    | VehicleFeeClaim
    | FbarClaim
    | BillClaim,
    Field(discriminator="category"),
]
CLAIM_ADAPTER: TypeAdapter[Claim] = TypeAdapter(Claim)
