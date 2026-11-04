"""Registry: taxpayers, institutions and sources (§3.1, §5.1, §5.2, §5.2a)."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from taxbinder.models.common import CustomDocType, DocId, DocType, Record, Slug, SourceId


class Taxpayer(Record):
    id: Slug
    name: str
    kind: Literal["individual", "trust", "entity"] = "individual"
    tin: str | None = Field(None, description="Encrypted in the vault (§4.6)")
    role: Literal["taxpayer", "spouse", "dependent"] | None = Field(
        None, description="In the household; None for a trust or not said"
    )
    last4: str | None = Field(None, pattern=r"^\d{4}$", description="Of the SSN/EIN; never more")
    notes: str = ""


class Portal(Record):
    url: str | None = None


class MonthDay(Record):
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)


class Lineage(Record):
    relation: str = Field(description="e.g. acquired_by, renamed_to, merged_into")
    institution: Slug
    effective: date | None = None


class Institution(Record):
    id: Slug
    name: str
    kind: Literal[
        "brokerage",
        "bank",
        "fund_sponsor",
        "real_estate_sponsor",
        "employer",
        "government",
        "charity",
        "daf",
        "other",
    ] = "other"
    tin: str | None = Field(None, description="EIN as printed on its forms; encrypted (§4.6)")
    portal: Portal | None = None
    typical_release: MonthDay | None = None
    aliases: list[str] = []
    lineage: list[Lineage] = []
    notes: str = ""


class Identifier(Record):
    """One account number (or other identifier) a Source has used (§5.2a)."""

    number: str = Field(description="`ENC[age:…]` on disk; plaintext only in memory")
    hint: str | None = Field(None, description="Masked display value, e.g. …2291")
    kind: Literal["primary", "legacy", "alternate", "reference"] = "primary"
    issuer: Slug | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    note: str | None = None
    source_doc: DocId | None = None

    def matches(self, printed: str) -> bool:
        """Does an account number as printed on a document ("XXXX-1234", "…1234") match?

        Full numbers must be equal (ignoring spaces and dashes); masked or partial prints
        match on their visible trailing digits (at least 4).
        """
        mine = re.sub(r"[\s-]", "", self.number).lower()
        seen = re.sub(r"[\s-]", "", printed).lower()
        if mine == seen:
            return True
        tail = re.search(r"(\d{4,})$", seen)
        masked = bool(re.search(r"[x*…•]", seen)) or len(seen) < len(mine)
        return bool(tail and masked and mine.endswith(tail.group(1)))

    @model_validator(mode="after")
    def _dates_ordered(self) -> Identifier:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to is before effective_from")
        return self


class Produces(Record):
    doc_type: DocType
    federal: bool | None = None
    states: list[str] | None = None
    optional: bool = False


class Source(Record):
    id: SourceId
    institution: Slug
    name: str
    owner: Slug
    identifiers: list[Identifier] = []
    opened: date | None = None
    closed: date | None = None
    first_tax_year: int | None = None
    last_tax_year: int | None = None
    produces: list[Produces] = []
    expected_each_year: bool = True
    tags: list[str] = []
    notes: str = ""

    @model_validator(mode="after")
    def _id_under_institution(self) -> Source:
        if not self.id.startswith(f"{self.institution}."):
            raise ValueError(f"source id {self.id!r} must start with '{self.institution}.'")
        return self


PackageFolder = Literal[
    "00-Returns",
    "01-W2",
    "02-1099",
    "03-K1",
    "04-1098-5498",
    "05-Estimated-Payments",
    "06-Donations",
    "07-Property-Vehicle",
    "08-Foreign",
    "09-Other",
]


class DocTypeDef(Record):
    """A document type the person added from the app or CLI (`registry/doc-types/<id>.yaml`)."""

    id: CustomDocType
    name: str = Field(min_length=1, max_length=60)
    folder: PackageFolder = "09-Other"
    keywords: list[str] = Field(
        default=[], description="Phrases that identify it on a document (case-insensitive)"
    )


ReturnForm = Literal["1040", "1120", "1120-S", "1065", "1041"]
RETURN_FORMS: dict[str, str] = {
    "1040": "Form 1040 · individual or joint",
    "1120": "Form 1120 · C corporation",
    "1120-S": "Form 1120-S · S corporation",
    "1065": "Form 1065 · partnership or LLC",
    "1041": "Form 1041 · trust or estate",
}


class EntityInfo(Record):
    """Who this vault files for (`registry/entity.yaml`); a vault without one is the household."""

    id: Literal["entity"] = "entity"
    name: str = Field(min_length=1, max_length=80)
    form: ReturnForm = "1040"


class AppSettings(Record):
    """`registry/settings.yaml` in the household's vault: the app's own settings.
    None means "not said": the app works it out from the vault (see `settings.py`)."""

    id: Literal["settings"] = "settings"
    other_filers: bool | None = Field(None, description="Trusts and businesses with own vaults")
    tick_documents: bool | None = Field(None, description="Verify documents one by one")
    websites_open_in: Literal["app_window", "browser"] = "app_window"
    usual_boxes: dict[str, str] = Field({}, description="Form → its usual box")
    dismissed_notices: list[str] = Field([], description="Noticed for you: notes set aside")
