"""Document: an immutable stored file and its metadata (§5.4, §3.5)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from taxbinder.models.common import DocId, DocType, Money, Record, Slug, SourceId

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
METHODS = {"filename", "rules", "llm", "human"}


class Acquired(Record):
    at: datetime
    via: str = Field(description="upload | drop_folder | portal_window | …")
    filename: str | None = None
    source_url: str | None = Field(None, description="Without query string (may hold tokens)")


class Classification(Record):
    doc_type: DocType | None = None
    tax_year: int | None = None
    issuer: Slug | None = None
    source: SourceId | None = None
    account_number: str | None = Field(
        None, description="As printed; encrypted in the vault (§4.6)"
    )
    issuer_tin: str | None = Field(
        None, description="The issuer's EIN as printed; encrypted in the vault (§4.6)"
    )
    confidence: float | None = Field(None, ge=0, le=1)
    fiscal_year_end: date | None = Field(
        None, description="A fiscal-year K-1: its tax year ends here, and is filed by it"
    )
    method: str | None = Field(None, description="`+`-joined: filename, rules, llm, human")
    confirmed_by: str | None = None

    @field_validator("method")
    @classmethod
    def _known_methods(cls, v: str | None) -> str | None:
        if v is not None and not set(v.split("+")) <= METHODS:
            raise ValueError(f"method must be '+'-joined from {sorted(METHODS)}")
        return v


class Version(Record):
    supersedes: DocId | None = None
    is_corrected: bool = False
    is_final: bool = True


FilingStatus = Literal[
    "single",
    "married_filing_jointly",
    "married_filing_separately",
    "head_of_household",
    "qualifying_surviving_spouse",
]


class ReturnPerson(Record):
    """Someone named on a 1040's first page."""

    name: str
    role: Literal["taxpayer", "spouse", "dependent"]
    last4: str | None = Field(None, pattern=r"^\d{4}$", description="Of the SSN; never more")


TotalOnly = Literal[
    "wages", "interest", "dividends", "retirement", "social_security", "mortgage_interest"
]
Paid = Literal["estimated", "property_tax", "vehicle", "gifts"]


class Listed(Record):
    """A payer or entity a return names (Schedule B, Schedule E), and the form it sends (a
    dividend payer's is a consolidated 1099 row, which also takes a 1099-DIV on its own)."""

    name: str = Field(description="As printed")
    ein: str | None = Field(None, description="As printed; encrypted in the vault")
    doc_type: DocType


class ReturnRead(Record):
    """What a 1040 says about the household and its documents: suggestions only,
    confirmed by a person before anything else uses them."""

    filing_status: FilingStatus | None = None
    people: list[ReturnPerson] = []
    state: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="From the address")
    listed: list[Listed] = []
    totals_only: list[TotalOnly] = Field(
        [], description="Income the return only totals (W-2 wages…): rows added by hand"
    )
    paid: list[Paid] = Field(
        [], description="What it shows was paid (estimated tax, property tax, gifts): never amounts"
    )


class ReturnInfo(Record):
    """What kind of tax return (or paper filed with one) a document is."""

    form: str = Field(description="As printed: 1040, 1040-X, 1065, IT-201, 540, 8879, 4868, …")
    jurisdiction: str = Field("federal", pattern=r"^(federal|[A-Z]{2})$")
    resident: bool | None = Field(None, description="State returns: resident or not, if printed")
    status: Literal["draft", "filed", "unsure"] = "unsure"
    amended: bool = False
    read: ReturnRead | None = Field(None, description="A 1040's contents")


class Recipient(Record):
    """Whose document it is: the person, both spouses, or a trust named on it."""

    printed: str | None = Field(None, description="The recipient's name as printed")
    taxpayers: list[Slug] = Field([], description="Who it's for: one person, both, or a trust")
    ask: bool = Field(False, description="Unclear (shared surname, unknown name): asked on review")


class RedactedVariant(Record):
    blob: str
    profile: str
    at: datetime


class DeliveryRecord(Record):
    to: str
    channel: str
    at: datetime
    package: str | None = None


class Document(Record):
    id: DocId
    sha256: Sha256
    blob: str
    original_filename: str
    mime: str
    pages: int | None = None
    bytes: int
    acquired: Acquired
    reacquired: list[Acquired] = Field(default=[], description="Later copies of the same bytes")
    classification: Classification | None = None
    version: Version = Version()
    tax_return: ReturnInfo | None = Field(None, description="Set for a return or return paper")
    recipient: Recipient | None = None
    ocr: Sha256 | None = Field(
        None, description="Its scanned pages were read on the Mac: the words' blob (encrypted)"
    )
    model_read: Sha256 | None = Field(
        None, description="What the reading model read on it, compared with the rules: "
        "the cross-check's blob (encrypted)"
    )
    redacted_variants: list[RedactedVariant] = []
    extraction: str | None = None
    delivery: list[DeliveryRecord] = []
    tags: list[str] = []
    waiting: bool = Field(
        False, description="Added through Add documents and not yet filed on Check"
    )
    preparer_note: str | None = Field(
        None, description="For my preparer: what the paper is for, in the person's words"
    )
    confirmed_boxes: dict[str, Money] = Field(
        default={}, description="Key boxes whose amount the person confirmed where the readers "
        "didn't agree, box → amount; wins over both readings"
    )

    @property
    def scanned(self) -> bool:
        """Read from a scan or photo: looked at more closely on review."""
        return self.ocr is not None

    @model_validator(mode="after")
    def _id_matches_hash(self) -> Document:
        if self.id != f"doc_{self.sha256[:12]}":
            raise ValueError(f"document id {self.id!r} must be 'doc_' + sha256[:12]")
        return self
