"""Across the years: its rows, and each year's figures (§11.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from taxbinder.models.common import DocId, DocType, Money, Record, SourceId


class MatrixRow(Record):
    source: SourceId
    doc_type: DocType
    boxes: list[str] = Field(default=[], description="e.g. ['1', '5']: one value per box")
    own: bool = Field(
        False, description="Box set by the person: changing the form's usual box leaves it"
    )

    @field_validator("boxes")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out = [" ".join(b.split()) for b in v if b.strip()]
        if len(out) > 6:
            raise ValueError("at most 6 boxes per row")
        if any(len(b) > 12 for b in out):
            raise ValueError("a box id is at most 12 characters, e.g. '1', '20 Z', 'AE'")
        return out


class MatrixLayout(Record):
    """`registry/matrix.yaml`: the rows, in order, once the person changes them."""

    id: Literal["matrix"] = "matrix"
    first_year: int | None = Field(None, ge=1990, le=2100)
    rows: list[MatrixRow] = []


class Figure(Record):
    source: SourceId
    doc_type: DocType
    values: list[Money | None] = Field(
        description="One per box. The first box's value lives on the year's checklist row "
        "(its Amount) when that row exists; it's stored here only when there's no such row."
    )


class Suggestion(Record):
    """Values read from the year's document, waiting for the person to accept or dismiss."""

    source: SourceId
    doc_type: DocType
    doc: DocId
    values: list[Money | None]  # one per box; None where the box couldn't be read
    boxes: list[str] = Field([], description="The boxes read (older ones: the row's at the time)")
    pages: list[int | None] = []
    scanned: bool = Field(False, description="Read from a scan: check it against the paper")
    checks: list[str] = Field(
        [],
        description="Why the values read need a look: a check of the form's arithmetic they fail "
        '("4 = 6.2% of 3"), or the rules and the model read them differently',
    )


class Dismissed(Record):
    source: SourceId
    doc_type: DocType
    doc: DocId


class FigureSet(Record):
    """`years/<year>/figures.yaml`."""

    year: int = Field(ge=1990, le=2100)
    figures: list[Figure] = []
    suggested: list[Suggestion] = []
    dismissed: list[Dismissed] = []  # documents whose suggestion the person turned down
