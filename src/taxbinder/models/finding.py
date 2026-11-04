"""Finding: output of a rule or analysis (§8.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from taxbinder.models.common import Record


class Finding(Record):
    id: str
    rule: str
    rule_version: str
    params_hash: str | None = None
    severity: Literal["error", "warning", "info"]
    classification: Literal[
        "missing_evidence", "change", "possible_inconsistency", "demonstrated_error"
    ]
    subjects: list[str]
    message: str
    evidence: list[str] = []
    suggested_action: str | None = None
    first_seen: datetime
    last_seen: datetime
    status: Literal["open", "acknowledged", "resolved"] = "open"
    resolution_note: str | None = None


class FindingSet(Record):
    """Latest reconcile run for a year: `years/<year>/findings.yaml`."""

    year: int
    ran_at: datetime
    params_hash: str
    findings: list[Finding] = []
