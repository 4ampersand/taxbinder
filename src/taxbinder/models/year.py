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


class TrackedReturn(Record):
    """Where one of the year's returns is, as a person says: filed or not, and what it came to.
    The filed copy itself is the year's return document marked Filed copy."""

    jurisdiction: str = Field("federal", pattern=r"^(federal|[A-Z]{2})$")
    status: Literal["not_started", "with_preparer", "filed", "accepted", "not_needed"] = (
        "not_started"
    )
    filed_on: date | None = None
    extended: bool = Field(False, description="An extension was filed (federal: Form 4868)")
    refund: Money | None = Field(None, description="What the return says comes back")
    owed: Money | None = Field(None, description="What the return says is still to pay")
    refund_arrived_on: date | None = None


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
    returns: list[TrackedReturn] = Field([], description="The year's returns, one per jurisdiction")
    asked: dict[str, Literal["no", "unsure"]] = Field(
        {}, description="Results' questions answered No or Not sure, ask my preparer"
    )
