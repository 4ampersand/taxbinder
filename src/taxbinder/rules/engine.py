"""Rule framework (§8.1).

A rule is a function over the year's context that yields either
- `Flag`: a finding about one or more subjects, or
- `Require`: evidence a claim needs (stored on the claim as a Requirement).

`run` evaluates every registered rule, decides which requirements are satisfied (§8.3: a linked
document of an accepted type and matching tax year, or a waiver) and turns unsatisfied ones into
`missing_evidence` findings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from taxbinder.models import Document, Expectation, Finding, Institution, Source, YearConfig
from taxbinder.models.claim import ClaimBase, Requirement
from taxbinder.models.common import DocType
from taxbinder.rules.params import Params

Severity = Literal["error", "warning", "info"]
Classification = Literal[
    "missing_evidence", "change", "possible_inconsistency", "demonstrated_error"
]


@dataclass(frozen=True)
class Flag:
    subjects: list[str]
    message: str
    key: str | None = None  # distinguishes several findings of one rule on one subject
    suggested_action: str | None = None
    evidence: list[str] = field(default_factory=list)
    classification: Classification | None = None  # overrides the rule's default


@dataclass(frozen=True)
class Require:
    claim: ClaimBase
    id: str
    accepts: list[DocType]
    message: str  # shown while unsatisfied


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: Severity
    classification: Classification
    fn: Callable[[Ctx], Iterable[Flag | Require]]
    version: str = "1"


RULES: dict[str, Rule] = {}


def rule(
    id: str, title: str, severity: Severity, classification: Classification, version: str = "1"
) -> Callable[
    [Callable[[Ctx], Iterable[Flag | Require]]], Callable[[Ctx], Iterable[Flag | Require]]
]:
    def register(fn: Callable[[Ctx], Iterable[Flag | Require]]) -> Any:
        if id in RULES:
            raise ValueError(f"rule {id} registered twice")
        RULES[id] = Rule(id, title, severity, classification, fn, version)
        return fn

    return register


@dataclass
class Ctx:
    """Everything a rule may look at for one tax year. Rules never write."""

    year: int
    params: Params
    today: date
    year_config: YearConfig
    institutions: list[Institution]
    sources: list[Source]
    expectations: list[Expectation]
    prior_expectations: list[Expectation]
    documents: list[Document]
    claims: list[Any]  # actual claims; planned / scheduled payments are in `planned`
    planned: list[Any] = field(default_factory=list)

    def param(self, rule_id: str, key: str) -> Any:
        return self.params.get(rule_id, key)

    def claims_of(self, category: str) -> list[Any]:
        return [c for c in self.claims if c.category == category]

    def doc(self, doc_id: str) -> Document | None:
        return next((d for d in self.documents if d.id == doc_id), None)

    def filing_deadline(self) -> date:
        if "filing" in self.year_config.deadlines:
            return self.year_config.deadlines["filing"]
        from taxbinder.returns import extended

        key = "extended_deadline" if extended(self.year_config, self.documents) else "deadline"
        return self.param("R-EXP-01", key)


def _satisfies(
    doc: Document | None, accepts: list[DocType], year: int, by_person: bool = False
) -> bool:
    """The document is of an accepted type and for this year. One a person attached may also be
    dated the next year (a January letter for a December gift), never an earlier one."""
    if doc is None or doc.classification is None:
        return False
    c = doc.classification
    years = (None, year, year + 1) if by_person else (None, year)
    return c.doc_type in accepts and c.tax_year in years


@dataclass
class Result:
    findings: list[Finding]
    requirements: dict[str, list[Requirement]]  # claim id → computed requirements


def run(ctx: Ctx, now: datetime, only: Iterable[str] | None = None) -> Result:
    findings: dict[str, Finding] = {}
    required: dict[str, list[tuple[Rule, Require]]] = {}
    selected = [RULES[r] for r in only] if only else list(RULES.values())

    def add(r: Rule, flag: Flag, classification: Classification | None = None) -> None:
        fid = f"{r.id}:{flag.subjects[0] if flag.subjects else ctx.year}"
        if flag.key:
            fid += f":{flag.key}"
        findings[fid] = Finding(
            id=fid,
            rule=r.id,
            rule_version=r.version,
            params_hash=ctx.params.hash,
            severity=r.severity,
            classification=flag.classification or classification or r.classification,
            subjects=flag.subjects,
            message=flag.message,
            evidence=flag.evidence,
            suggested_action=flag.suggested_action,
            first_seen=now,
            last_seen=now,
        )

    for r in selected:
        for hit in r.fn(ctx):
            if isinstance(hit, Require):
                required.setdefault(hit.claim.id, []).append((r, hit))
            else:
                add(r, hit)

    requirements: dict[str, list[Requirement]] = {}
    claims = {c.id: c for c in ctx.claims}
    for claim_id, reqs in required.items():
        claim = claims[claim_id]
        existing = {q.id: q for q in claim.requirements}
        linked = [link.doc for link in claim.evidence if link.role != "superseded"]
        by_person = {link.doc for link in claim.evidence if not link.linked_by.startswith("auto:")}
        out = []
        for r, req in reqs:
            prev = existing.get(req.id)
            satisfied_by = prev.satisfied_by if prev else None
            if satisfied_by is None:
                satisfied_by = next(
                    (
                        d
                        for d in linked
                        if _satisfies(ctx.doc(d), req.accepts, ctx.year, d in by_person)
                    ),
                    None,
                )
            waived = prev.waived if prev else None
            out.append(
                Requirement(
                    id=req.id,
                    rule=r.id,
                    accepts=req.accepts,
                    satisfied_by=satisfied_by,
                    waived=waived,
                )
            )
            if satisfied_by is None and waived is None:
                add(
                    r,
                    Flag([claim_id], req.message, key=req.id, suggested_action=_accepts(req)),
                    "missing_evidence",
                )
        requirements[claim_id] = out
    for claim in ctx.claims:  # claims that no longer need anything
        requirements.setdefault(claim.id, [])
    return Result(sorted(findings.values(), key=lambda f: f.id), requirements)


def _accepts(req: Require) -> str:
    return "Link a document of type " + " or ".join(req.accepts)


def load_catalog() -> dict[str, Rule]:
    """Import the catalog modules so their rules register."""
    from taxbinder.rules.catalog import (  # noqa: F401
        consistency,
        donations,
        expectations,
        foreign,
        payments,
        returns,
        sources,
    )

    return RULES
