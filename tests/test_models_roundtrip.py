"""YAML → model → YAML is stable for every §5 example (and a few synthetic ones)."""

from pathlib import Path
from typing import Any

import pytest

from taxbinder.models import (
    Claim,
    Document,
    Expectation,
    Finding,
    Institution,
    Source,
    Taxpayer,
    YearConfig,
)
from taxbinder.yamlio import from_yaml, load_yaml, to_data, to_yaml

EXAMPLES = Path(__file__).parent / "fixtures" / "examples"
SHAPES: dict[str, Any] = {
    "taxpayer": Taxpayer,
    "institution": Institution,
    "source": Source,
    "year": YearConfig,
    "expectation": Expectation,
    "document": Document,
    "claim": Claim,
    "finding": Finding,
}
FILES = sorted(EXAMPLES.glob("*.yaml"))


def test_every_kind_has_an_example() -> None:
    assert {f.name.split(".")[0] for f in FILES} == set(SHAPES)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_round_trip(path: Path) -> None:
    text = path.read_text()
    shape = SHAPES[path.name.split(".")[0]]

    record = from_yaml(text, shape)
    assert to_data(record) == load_yaml(text)  # nothing lost, added or changed

    written = to_yaml(record)
    assert from_yaml(written, shape) == record
    assert to_yaml(from_yaml(written, shape)) == written  # text is stable
