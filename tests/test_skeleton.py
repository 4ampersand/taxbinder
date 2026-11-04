import importlib

import pytest

MODULES = [
    "taxbinder",
    "taxbinder.models",
    "taxbinder.store",
    "taxbinder.ingest",
    "taxbinder.redact",
    "taxbinder.extract",
    "taxbinder.rules",
    "taxbinder.package",
    "taxbinder.web",
    "taxbinder.sidecar",
    "taxbinder.cli",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_version() -> None:
    import re
    from pathlib import Path

    import taxbinder

    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert taxbinder.__version__ == re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
