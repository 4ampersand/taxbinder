"""Where each record type lives in `records/` (§4.2, §4.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints, TypeAdapter

from taxbinder.models import (
    AppSettings,
    Claim,
    DocTypeDef,
    Document,
    EntityInfo,
    Expectation,
    FigureSet,
    FindingSet,
    Institution,
    MatrixLayout,
    PrivacyList,
    PseudonymMap,
    Source,
    Taxpayer,
    YearConfig,
)
from taxbinder.models.claim import ClaimBase
from taxbinder.models.common import ClaimId, CustomDocType, DocId, ExpectationId, Slug, SourceId

YearId = Annotated[str, StringConstraints(pattern=r"^\d{4}$")]


@dataclass(frozen=True)
class Kind:
    name: str
    shape: Any
    id_type: Any  # validates ids before they become paths
    path: Callable[[str], str]  # record id → path relative to records/
    glob: Callable[[int | None], str]  # year (or all) → glob relative to records/

    @property
    def adapter(self) -> TypeAdapter[Any]:
        return TypeAdapter(self.shape)

    def check_id(self, record_id: str) -> None:
        TypeAdapter(self.id_type).validate_python(record_id)


def _year_dir(sub: str) -> Callable[[int | None], str]:
    return lambda year: f"years/{year if year is not None else '*'}/{sub}"


def _in_year(sub: str) -> Callable[[str], str]:
    """`<year>/<rest>` ids → `years/<year>/<sub>/<rest>.yaml`."""

    def path(record_id: str) -> str:
        year, _, rest = record_id.partition("/")
        return f"years/{year}/{sub}/{rest}.yaml"

    return path


KINDS: dict[str, Kind] = {
    k.name: k
    for k in [
        Kind(
            "taxpayer",
            Taxpayer,
            Slug,
            lambda i: f"registry/taxpayers/{i}.yaml",
            lambda _: "registry/taxpayers/*.yaml",
        ),
        Kind(
            "institution",
            Institution,
            Slug,
            lambda i: f"registry/institutions/{i}.yaml",
            lambda _: "registry/institutions/*.yaml",
        ),
        Kind(
            "entity",
            EntityInfo,
            Annotated[str, StringConstraints(pattern=r"^entity$")],
            lambda _: "registry/entity.yaml",
            lambda _: "registry/entity.yaml",
        ),
        Kind(
            "settings",
            AppSettings,
            Annotated[str, StringConstraints(pattern=r"^settings$")],
            lambda _: "registry/settings.yaml",
            lambda _: "registry/settings.yaml",
        ),
        Kind(
            "matrix",
            MatrixLayout,
            Annotated[str, StringConstraints(pattern=r"^matrix$")],
            lambda _: "registry/matrix.yaml",
            lambda _: "registry/matrix.yaml",
        ),
        Kind(
            "figures",
            FigureSet,
            YearId,
            lambda i: f"years/{i}/figures.yaml",
            _year_dir("figures.yaml"),
        ),
        Kind(
            "privacy",
            PrivacyList,
            Annotated[str, StringConstraints(pattern=r"^privacy$")],
            lambda _: "registry/privacy.yaml",
            lambda _: "registry/privacy.yaml",
        ),
        Kind(
            "pseudonyms",
            PseudonymMap,
            Annotated[str, StringConstraints(pattern=r"^pseudonyms$")],
            lambda _: "registry/pseudonyms.yaml",
            lambda _: "registry/pseudonyms.yaml",
        ),
        Kind(
            "doctype",
            DocTypeDef,
            CustomDocType,
            lambda i: f"registry/doc-types/{i}.yaml",
            lambda _: "registry/doc-types/*.yaml",
        ),
        Kind(
            "source",
            Source,
            SourceId,
            lambda i: f"registry/sources/{i}.yaml",
            lambda _: "registry/sources/*.yaml",
        ),
        Kind("year", YearConfig, YearId, lambda i: f"years/{i}/year.yaml", _year_dir("year.yaml")),
        Kind(
            "expectation",
            Expectation,
            ExpectationId,
            _in_year("expectations"),
            _year_dir("expectations/*.yaml"),
        ),
        Kind(
            "document",
            Document,
            DocId,
            lambda i: f"documents/{i}.yaml",
            lambda _: "documents/*.yaml",
        ),
        Kind("claim", Claim, ClaimId, _in_year("claims"), _year_dir("claims/*/*.yaml")),
        Kind(
            "findings",
            FindingSet,
            YearId,
            lambda i: f"years/{i}/findings.yaml",
            _year_dir("findings.yaml"),
        ),
    ]
}


def kind_of(record: BaseModel) -> Kind:
    for name, types in [
        ("taxpayer", Taxpayer),
        ("institution", Institution),
        ("entity", EntityInfo),
        ("matrix", MatrixLayout),
        ("settings", AppSettings),
        ("figures", FigureSet),
        ("privacy", PrivacyList),
        ("pseudonyms", PseudonymMap),
        ("doctype", DocTypeDef),
        ("source", Source),
        ("year", YearConfig),
        ("expectation", Expectation),
        ("document", Document),
        ("claim", ClaimBase),
        ("findings", FindingSet),
    ]:
        if isinstance(record, types):
            return KINDS[name]
    raise TypeError(f"not a stored record type: {type(record).__name__}")


def record_id(record: BaseModel) -> str:
    if isinstance(record, YearConfig | FindingSet | FigureSet):
        return str(record.year)
    return record.id  # type: ignore[attr-defined]


def record_year(record: BaseModel) -> int | None:
    if isinstance(record, Document):
        return record.classification.tax_year if record.classification else None
    return getattr(record, "year", None)
