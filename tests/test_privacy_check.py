"""The privacy check, on the fictional fixture vault and a
throwaway git repository. Never on the real vault."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from openpyxl import Workbook

from taxbinder import backup
from taxbinder.dev.privacy_check import BUILT_IN, check, main, vault_finders
from taxbinder.entities import list_entities
from taxbinder.models import Institution, Source
from taxbinder.redact.detect import B, E, Finder, _number
from taxbinder.store import RecordStore, keys_from_env, open_vault, restore_key

FIXTURE = Path(__file__).parent / "fixtures" / "vaults" / "format-2.tbak"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """The fixture vault (Avery and Jordan Quill, 900-00-0101, Tamarack Savings Bank, …)."""
    out = tmp_path / "vault"
    key = FIXTURE.with_suffix(".key").read_text().splitlines()[-1]
    backup.restore(FIXTURE, key, out / "restored")
    household = out / "restored" / "vault"
    for ref in list_entities(household):
        restore_key(ref.path, keys_from_env(), key)
    return household


@pytest.fixture
def finders(vault: Path) -> list:
    return vault_finders(vault, keys_from_env()) + BUILT_IN


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("A fictional household: invented names only.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def commit(repo: Path, files: dict[str, str | bytes], message: str = "change") -> None:
    for name, content in files.items():
        path = repo / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def test_a_clean_repository_has_no_hits(repo: Path, finders: list) -> None:
    assert check(repo, finders) == []


def test_a_name_in_a_file(repo: Path, finders: list) -> None:
    commit(repo, {"notes.md": "line one\nmailed by Jordan Quill\n"})
    places = {h.place for h in check(repo, finders)}
    assert "notes.md:2" in places


def test_names_and_numbers_in_other_forms(repo: Path, finders: list) -> None:
    commit(repo, {"a.py": "x = 'Quill, Avery'\n", "b.py": "tin = '900000101'\n"})
    places = {h.place for h in check(repo, finders)}
    assert {"a.py:1", "b.py:1"} <= places


def test_an_institution(repo: Path, finders: list) -> None:
    commit(repo, {"c.md": "statements from tamarack savings bank\n"})
    hits = check(repo, finders)
    assert hits and all(h.place == "c.md:1" and h.label.startswith("INST") for h in hits)


def test_only_in_an_earlier_version(repo: Path, finders: list) -> None:
    commit(repo, {"old.md": "Avery Quill\n"})
    commit(repo, {"old.md": "nobody\n"})
    hits = check(repo, finders)
    assert hits and all(h.place.startswith("old.md:1 (earlier version ") for h in hits)


def test_in_a_commit_message(repo: Path, finders: list) -> None:
    commit(repo, {"d.md": "nothing here\n"}, message="add Rowan Quill's forms")
    hits = check(repo, finders)
    assert hits and all(h.place.endswith("message") for h in hits)


def test_inside_a_workbook(repo: Path, finders: list, tmp_path: Path) -> None:
    wb = Workbook()
    wb.active["B3"] = "Tamarack Savings Bank"
    wb.save(tmp_path / "book.xlsx")
    commit(repo, {"book.xlsx": (tmp_path / "book.xlsx").read_bytes()})
    hits = check(repo, finders)
    assert hits and all(h.place.startswith("book.xlsx:") for h in hits)


def test_built_in_shapes_outside_the_fictional_ranges(repo: Path) -> None:
    ssn, ein = "123-45-" + "6789", "12-" + "3456789"  # built here, so this file has no such shape
    commit(repo, {"e.txt": f"{ssn}\n{ein}\n900-12-3456\n00-1234567\n"})
    hits = check(repo, BUILT_IN)
    assert [(h.place, h.label) for h in hits] == [
        ("e.txt:1", "SSN-shaped"),
        ("e.txt:2", "EIN-shaped"),
    ]


def test_output_names_places_not_values(
    repo: Path, vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"f.md": "Avery Quill, 900-00-0101, Tamarack Savings Bank\n"})
    assert main(["--vault", str(vault), "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "f.md:1" in out and "PERSON" in out
    for value in ("Avery", "Quill", "0101", "Tamarack"):
        assert value not in out


def test_the_vault_is_only_read_from_a_copy(vault: Path) -> None:
    before = {p: p.read_bytes() for p in vault.parent.rglob("*") if p.is_file()}
    vault_finders(vault, keys_from_env())
    assert {p: p.read_bytes() for p in vault.parent.rglob("*") if p.is_file()} == before


def test_an_unreadable_vault_stops_the_check(
    repo: Path, vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys_from_env()._keys.clear()  # type: ignore[attr-defined]  # no key on this Mac
    assert main(["--vault", str(vault), "--repo", str(repo)]) == 2
    assert "can't read the vault" in capsys.readouterr().out


def test_without_a_vault_only_the_shapes_are_checked(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"g.txt": "Avery Quill 123-45-" + "6789\n"})
    assert main(["--vault", str(tmp_path / "none"), "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "only the built-in checks" in out and "g.txt:1  SSN-shaped" in out
    assert "PERSON" not in out


def test_the_authors_name_only_as_author(repo: Path, finders: list) -> None:
    git(repo, "config", "user.name", "Avery Quill")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=a@example.com", "-c", "commit.gpgsign=false",
         "commit", "-q", "--allow-empty", "-m", "by the maintainer"],
        check=True, capture_output=True,
    )  # fmt: skip
    commit(repo, {"NOTICE": "Copyright 2026 Avery Quill\n", "t.py": "who = 'Avery Quill'\n"})
    assert {h.place.split(":")[0] for h in check(repo, finders)} == {"t.py"}


def test_tax_agencies_are_not_household_details(repo: Path, vault: Path) -> None:
    with RecordStore(open_vault(vault, keys_from_env())) as store, store.operation("t", "x"):
        store.put(Institution(id="irs", name="IRS", kind="other"), None)
        store.put(Institution(id="ftb", name="Hollis Tax Board", kind="government"), None)
        store.put(Institution(id="eftps", name="EFTPS", kind="other"), None)
        store.put(Institution(id="or", name="State of Oregon", kind="other"), None)
        store.put(Source(id="irs.ssa", institution="irs", name="Social Security Administration",
                         owner="me"), None)  # fmt: skip
    commit(repo, {"h.md": "IRS, Hollis Tax Board, EFTPS, State of Oregon\n"
                          "Social Security Administration\n"})  # fmt: skip
    assert check(repo, vault_finders(vault, keys_from_env())) == []


def test_a_number_that_is_only_a_year_or_four_digits(repo: Path) -> None:
    tail = [Finder(re.compile(f"{B}{n}{E}"), "ACCT", f"ACCT_{n}", True) for n in ("2026", "5498")]
    tail.append(Finder(re.compile(_number("5498-SA"), re.I), "ACCT", "ACCT_SA", True))
    ending = Finder(re.compile(rf"(?:…|ending\s+){'5531'}{E}"), "ACCT", "ACCT_5531", True)
    commit(repo, {"y.py": "reconcile(store, 2026)\n", "w.py": '"Form 5498-SA", "5498_sa"\n',
                  "z.md": "account ending 5531\n", "v.md": "the account …5531\n"})  # fmt: skip
    assert sorted(h.place for h in check(repo, [*tail, ending])) == ["v.md:1", "z.md:1"]


def test_the_copyright_line_in_help(repo: Path, finders: list) -> None:
    git(repo, "config", "user.name", "Avery Quill")
    help_page = repo / "src" / "taxbinder" / "web" / "templates"
    help_page.mkdir(parents=True)
    commit(repo, {"src/taxbinder/web/templates/help.html": "Copyright 2026 Avery Quill\n"})
    assert check(repo, finders) == []
    commit(repo, {"src/taxbinder/web/templates/help.html": "Copyright 2026 Avery Quill\nmore\n"})
    assert check(repo, finders) == []  # nor in an earlier version of the page
    commit(repo, {"t.py": "who = 'Avery Quill'\n"})
    commit(repo, {"t.py": "who = 'someone'\n"})
    assert {h.place.split(":")[0] for h in check(repo, finders)} == {"t.py"}  # elsewhere: a hit


def test_source_names_but_not_generic_ones(repo: Path, finders: list) -> None:
    commit(repo, {"s.md": "the Alderpoint Timber Fund K-1\njoint savings 1099-INT\n"})
    hits = check(repo, finders)
    assert {(h.place, h.label[:3]) for h in hits} == {("s.md:1", "SRC")}
