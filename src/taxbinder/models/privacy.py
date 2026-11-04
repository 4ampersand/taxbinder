"""Private details and the redaction placeholder map (§9). Both live in the vault; every
`secret` is encrypted on disk (`store/crypto.py`)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from taxbinder.models.common import Record

PrivateKind = Literal["name", "address", "number", "email", "phone", "other"]


class PrivateValue(Record):
    """Something to hide that the vault doesn't otherwise hold (a name, an address…)."""

    kind: PrivateKind
    secret: str = Field(min_length=2, max_length=300, description="Encrypted in the vault")
    note: str = Field("", max_length=80, description="Who or what it is, e.g. 'daughter'")

    @field_validator("secret")
    @classmethod
    def _stripped(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("too short to hide reliably")
        return v


class PrivacyList(Record):
    """`registry/privacy.yaml`: names, addresses and numbers to hide in anything sent out."""

    id: Literal["privacy"] = "privacy"
    values: list[PrivateValue] = []


class Placeholder(Record):
    token: str = Field(pattern=r"^[A-Z]+_\d{2,}$")  # e.g. TIN_01, ACCT_07
    secret: str = Field(description="The real value (normalised); encrypted in the vault")


class PseudonymMap(Record):
    """`registry/pseudonyms.yaml`: each real value always gets the same placeholder, in every
    document and year (§9.3). Never exported."""

    id: Literal["pseudonyms"] = "pseudonyms"
    entries: list[Placeholder] = []
