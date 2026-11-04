"""Pydantic domain models: the single source of truth for record shapes (§5)."""

from taxbinder.models.claim import CLAIM_ADAPTER, Claim
from taxbinder.models.document import Document
from taxbinder.models.expectation import Expectation
from taxbinder.models.finding import Finding, FindingSet
from taxbinder.models.matrix import FigureSet, MatrixLayout
from taxbinder.models.privacy import PrivacyList, PseudonymMap
from taxbinder.models.registry import (
    AppSettings,
    DocTypeDef,
    EntityInfo,
    Institution,
    Source,
    Taxpayer,
)
from taxbinder.models.year import YearConfig

__all__ = [
    "CLAIM_ADAPTER",
    "Claim",
    "DocTypeDef",
    "Document",
    "AppSettings",
    "EntityInfo",
    "Expectation",
    "FigureSet",
    "Finding",
    "FindingSet",
    "Institution",
    "MatrixLayout",
    "PrivacyList",
    "PseudonymMap",
    "Source",
    "Taxpayer",
    "YearConfig",
]
