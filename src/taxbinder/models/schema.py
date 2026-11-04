"""JSON Schema export of every record type (§5), for editors and tools."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from taxbinder.models import (
    AppSettings,
    Claim,
    DocTypeDef,
    Document,
    EntityInfo,
    Expectation,
    FigureSet,
    Finding,
    FindingSet,
    Institution,
    MatrixLayout,
    PrivacyList,
    PseudonymMap,
    Source,
    Taxpayer,
    YearConfig,
)

TOP_LEVEL: dict[str, object] = {
    "Taxpayer": Taxpayer,
    "Institution": Institution,
    "Source": Source,
    "DocTypeDef": DocTypeDef,
    "EntityInfo": EntityInfo,
    "AppSettings": AppSettings,
    "MatrixLayout": MatrixLayout,
    "FigureSet": FigureSet,
    "PrivacyList": PrivacyList,
    "PseudonymMap": PseudonymMap,
    "YearConfig": YearConfig,
    "Expectation": Expectation,
    "Document": Document,
    "Claim": Claim,
    "Finding": Finding,
    "FindingSet": FindingSet,
}


def export_schemas(out_dir: Path) -> list[Path]:
    """Write `<out_dir>/<Name>.json` for every top-level record type."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, shape in TOP_LEVEL.items():
        schema = TypeAdapter(shape).json_schema(by_alias=True)
        schema.setdefault("title", name)
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written
