"""Per-year settings: `years/<year>/year.yaml` (§4.2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from taxbinder.models.common import Money, Record


class Preparer(Record):
    name: str
    firm: str | None = None
    channel: str | None = None  # how the preparer takes documents, e.g. their portal


class PackageMade(Record):
    """The last .zip for the preparer: when, and which documents went in. Never "sent":
    TaxBinder can't know that."""

    made_at: datetime
    documents: list[str] = []


class Summary(Record):
    """A hand-entered total to check detail against (§15), e.g. the checklist's federal
    estimates figure. `subject` is an expectation id or `<claim category>/<qualifier>`."""

    subject: str
    value: Money
    source: str


class YearConfig(Record):
    year: int
    filing_status: (
        Literal[
            "single",
            "married_filing_jointly",
            "married_filing_separately",
            "head_of_household",
            "qualifying_surviving_spouse",
        ]
        | None
    ) = None
    extension_filed: bool = False
    resident_states: list[str] = []
    moved_on: date | None = Field(
        None, description="A move: the first state until this day, the second from it"
    )
    deadlines: dict[str, date] = {}
    preparer: Preparer | None = None
    summaries: list[Summary] = []
    notes: str = ""
    package: PackageMade | None = None
    asked: dict[str, Literal["no", "unsure"]] = Field(
        {}, description="Results' questions answered No or Not sure, ask my preparer"
    )
