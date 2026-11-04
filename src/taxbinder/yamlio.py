"""YAML read/write for records (ruamel.yaml, safe mode).

Records are written with only the fields that were set, so a hand-edited file keeps its
shape. Comments are not preserved.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from pydantic import BaseModel, TypeAdapter
from ruamel.yaml import YAML
from ruamel.yaml.representer import SafeRepresenter


def _represent_datetime(rep: SafeRepresenter, value: datetime) -> Any:
    return rep.represent_scalar("tag:yaml.org,2002:timestamp", value.isoformat())


def _yaml() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    y.allow_unicode = True
    y.sort_base_mapping_type_on_output = False
    y.width = 100
    y.representer.add_representer(datetime, _represent_datetime)
    return y


def load_yaml(text: str) -> Any:
    return _yaml().load(text)


def dump_yaml(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def to_data(record: BaseModel) -> dict[str, Any]:
    """Plain data for a record: set fields only, aliases applied, amounts as numbers."""
    return record.model_dump(mode="python", by_alias=True, exclude_unset=True)


def to_yaml(record: BaseModel) -> str:
    return dump_yaml(to_data(record))


def from_yaml[T](text: str, shape: type[T] | Any) -> T:
    """Validate YAML text as `shape` (a model class, or a union such as `Claim`)."""
    return TypeAdapter(shape).validate_python(load_yaml(text))
