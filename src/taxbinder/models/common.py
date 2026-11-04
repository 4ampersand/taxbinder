"""Shared building blocks: base model, identifier types, money, evidence links (§4.3, §4.4)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SOURCE = rf"{SLUG}(?:\.{SLUG})+"

Slug = Annotated[str, StringConstraints(pattern=rf"^{SLUG}$")]
"""Institution, taxpayer and other kebab-case ids, e.g. `fernhollow-properties`."""

SourceId = Annotated[str, StringConstraints(pattern=rf"^{SOURCE}$")]
"""`<institution>.<slug>[.<slug>…]`, e.g. `plover-advisors.thornbury.individual`."""

DocId = Annotated[str, StringConstraints(pattern=r"^doc_[0-9a-f]{12}$")]
"""`doc_` + first 12 hex chars of the plaintext SHA-256."""

ExpectationId = Annotated[str, StringConstraints(pattern=rf"^\d{{4}}/{SOURCE}$")]
"""`<year>/<source>.<doctype slug>`, e.g. `2025/fernhollow-properties.kettle-410.k1`."""

ClaimId = Annotated[str, StringConstraints(pattern=rf"^\d{{4}}/{SLUG}/[a-z0-9][a-z0-9.-]*$")]
"""`<year>/<category dir>/<slug>`, e.g. `2025/donations/2025-02-26.friends-of-the-marsh-wrens`."""

RuleId = Annotated[str, StringConstraints(pattern=r"^R-[A-Z]+-\d{2}$")]


def _yaml_number(value: Decimal) -> int | float:
    """Write amounts as plain YAML numbers (ints stay ints)."""
    return int(value) if value == value.to_integral_value() else float(value)


Money = Annotated[Decimal, PlainSerializer(_yaml_number)]
"""Amounts and other exact quantities: Decimal in memory, never float."""

When = datetime | date
"""A timestamp, or just a day when that's all a person wrote down."""

BuiltinDocType = Literal[
    "w2",
    "1099_composite",
    "1099_int",
    "1099_div",
    "1099_q",
    "1099_r",
    "1099_misc",
    "1099_nec",
    "ssa_1099",
    "1098",
    "5498",
    "5498_sa",
    "k1_1065",
    "k1_state",
    "k1_1120s",
    "k1_1041",
    "donation_ack",
    "form_8283_support",
    "payment_confirmation",
    "property_tax_bill",
    "vehicle_registration",
    "account_change_notice",
    "foreign_bank_statement",
    "fbar_filing",
    "tax_return",
    "return_paper",
]
"""Appendix A document type catalog."""
BUILTIN_DOC_TYPES: tuple[str, ...] = get_args(BuiltinDocType)

CustomDocType = Annotated[str, StringConstraints(pattern=rf"^x-{SLUG}$")]
"""A document type the person added (`x-form-592-b`), defined by a `doctype` record (§5)."""

DocType = BuiltinDocType | CustomDocType


class Record(BaseModel):
    """Base for every stored shape. Unknown keys are errors, so typos fail loudly."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)


class Link(Record):
    """Evidence link, stored on the side that needs evidence (§4.4)."""

    doc: DocId
    role: str = "primary"
    linked_at: When
    linked_by: str = Field(description="`<user>` or `auto:<component>`")
