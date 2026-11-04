"""Per-tax-year rule parameters (§8.1a), shipped with the package."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from taxbinder.yamlio import load_yaml


@dataclass(frozen=True)
class Params:
    year: int
    values: dict[str, Any]
    hash: str

    def get(self, rule_id: str, key: str) -> Any:
        try:
            return self.values[rule_id][key]
        except KeyError:
            raise KeyError(f"rules/params/{self.year}.yaml has no {rule_id}.{key}") from None

    @property
    def reviewed(self) -> bool:
        return bool(self.values.get("reviewed_by"))


def params_file(year: int, form: str = "1040") -> str:
    """`2025.yaml` for Form 1040, `2025-1120.yaml`, `2025-1120-s.yaml` … for the others."""
    return f"{year}.yaml" if form == "1040" else f"{year}-{form.lower()}.yaml"


def load_params(year: int, form: str = "1040") -> Params:
    name = params_file(year, form)
    try:
        raw = resources.files(__package__).joinpath(name).read_bytes()
    except FileNotFoundError:
        raise ValueError(
            f"no rule parameters for {year}, Form {form} (rules/params/{name}); "
            "checks can't run yet"
        ) from None
    return Params(year, load_yaml(raw.decode()), "sha256:" + hashlib.sha256(raw).hexdigest()[:16])


def checked_years(form: str = "1040") -> list[int]:
    """The tax years with rule parameters for this return form: the years the app checks."""
    out = []
    for f in resources.files(__package__).iterdir():
        m = re.fullmatch(r"(\d{4})(?:-([a-z0-9-]+))?\.yaml", f.name)
        if m and (m.group(2) or "1040") == form.lower():
            out.append(int(m.group(1)))
    return sorted(out)
