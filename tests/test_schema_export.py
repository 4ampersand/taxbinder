from pathlib import Path

from typer.testing import CliRunner

from taxbinder.cli import app
from taxbinder.models.schema import TOP_LEVEL

REPO_SCHEMAS = Path(__file__).parents[1] / "schemas"


def test_schema_export(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["schema", "export", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == sorted(TOP_LEVEL)

    # The committed schemas/ must match the models (re-run `taxbinder schema export`).
    for path in tmp_path.glob("*.json"):
        assert path.read_text() == (REPO_SCHEMAS / path.name).read_text(), path.name
