"""The documentation: every "§" the code cites is a section of docs/architecture.md, docs/cli.md
is the command line's own help, and every link between the documents leads somewhere."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture.md"
CITED = re.compile(r"§ ?(\d+(?:\.\d+)?[a-z]?)")
DOCS = [*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md")]


def test_every_cited_section_is_in_the_architecture() -> None:
    headings = set(re.findall(r"^#{2,4} (\d+(?:\.\d+)?[a-z]?)\.? ", ARCHITECTURE.read_text(), re.M))
    cited = set()
    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "src").rglob("*.yaml"),
                 *(ROOT / "tests").rglob("*.py"),
                 *(ROOT / "tests" / "fixtures" / "examples").glob("*.yaml")]:  # fmt: skip
        cited |= set(CITED.findall(path.read_text()))
    assert cited and sorted(cited - headings) == []


def test_every_link_between_the_documents_leads_somewhere() -> None:
    links = 0
    for doc in DOCS:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text()):
            if not re.match(r"[a-z]+:", target):  # not https: or mailto:
                links += 1
                assert (doc.parent / target).exists(), (doc.name, target)
    assert links > 10


def test_the_cli_reference_is_current(tmp_path: Path) -> None:
    """docs/cli.md is `make cli-docs`'s output; run that after changing a command's help."""
    out = tmp_path / "cli.md"
    env = {**os.environ, "TAXBINDER_VAULT": "/nonexistent"}
    subprocess.run(
        [sys.executable, "-m", "typer", "taxbinder.cli", "utils", "docs", "--name", "taxbinder",
         "--output", str(out)],
        check=True, capture_output=True, env=env,
    )  # fmt: skip
    assert out.read_text() == (ROOT / "docs" / "cli.md").read_text(), "run `make cli-docs`"
