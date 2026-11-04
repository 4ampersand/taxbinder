"""Expectation: "Source S should produce doc type T for tax year Y" (§3.3, §5.3, §7.2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import model_validator

from taxbinder.models.common import DocType, ExpectationId, Link, Money, Record, SourceId, When

Applicability = Literal[
    "applicable",
    "not_applicable",
    "not_issued",
    "deferred_next_year",
    "below_threshold",
    "final_year_prior",
    "consolidated_elsewhere",
    "other",
]
Acquisition = Literal["expected", "available", "received", "vaulted", "superseded"]
Review = Literal["unreviewed", "extracted", "needs_attention", "verified"]
Delivery = Literal["not_sent", "sent", "stale"]


class LastCheck(Record):
    """Last attempt to find the document; `not_found` and `could_not_check` differ (§7.2)."""

    at: datetime
    via: str
    result: Literal["found", "not_found", "could_not_check"]
    detail: str | None = None


class Event(Record):
    at: When
    type: str
    text: str | None = None


class Expectation(Record):
    id: ExpectationId
    year: int
    source: SourceId
    doc_type: DocType
    applicability: Applicability = "applicable"
    applicability_note: str | None = None
    acquisition: Acquisition = "expected"
    review: Review = "unreviewed"
    delivery: Delivery = "not_sent"
    split_reporting: bool = False
    expected_available_at: date | None = None
    follow_up_at: date | None = None
    last_check: LastCheck | None = None
    events: list[Event] = []
    evidence: list[Link] = []
    due_by: date | None = None
    priority: Literal["low", "normal", "high"] | None = None
    amount: Money | None = None  # e.g. the K-1's income, a payment; typed in the checklist
    comment: str = ""

    @model_validator(mode="after")
    def _check(self) -> Expectation:
        prefix = f"{self.year}/{self.source}."
        if not self.id.startswith(prefix) or "." in self.id[len(prefix) :]:
            raise ValueError(f"expectation id {self.id!r} must be '{prefix}<slug>'")
        if self.applicability != "applicable" and not (self.applicability_note or "").strip():
            raise ValueError(f"applicability {self.applicability!r} requires applicability_note")
        return self
