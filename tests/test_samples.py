"""The fictional sample set generates, matches its expected answers
and holds only invented identifiers."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest
from samples.generate import NEW_TYPES, YEARS, generate
from samples.score import score

from taxbinder.models.common import BUILTIN_DOC_TYPES


@pytest.fixture(scope="module")
def sample_set(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("samples")
    return out, generate(out)


def test_files_match_the_expected_answers(sample_set: tuple[Path, dict]) -> None:
    out, expected = sample_set
    listed = {a["file"] for a in expected["documents"]} | {o["file"] for o in expected["others"]}
    assert {p.name for p in out.iterdir()} - {"expected.yaml"} == listed
    copies = {a["copy_of"] for a in expected["documents"] if "copy_of" in a}
    assert copies <= listed


def test_answers_are_valid(sample_set: tuple[Path, dict]) -> None:
    _, expected = sample_set
    for a in expected["documents"]:
        assert a["doc_type"] in BUILTIN_DOC_TYPES or a["doc_type"] in NEW_TYPES, a
        assert a["year"] in YEARS, a
        if a["doc_type"] != "tax_return":
            assert a["issuer"] in expected["issuers"], a
            assert a["filer"] in expected["filers"], a
    packet = next(a for a in expected["documents"] if a.get("form") == "1040" and a["year"] == 2024)
    assert len(packet["named_sources"]) == 18  # 3 Schedule B payers, 14 entities, 1 trust
    assert len(packet["missing_documents"]) == 2


def test_only_invented_identifiers(sample_set: tuple[Path, dict]) -> None:
    """SSNs are 900-00-xxxx (never issued) and EINs start with 00 (never assigned)."""
    out, _ = sample_set
    for path in out.glob("*.pdf"):
        try:
            pdf = pymupdf.open(path)
        except pymupdf.FileDataError:
            continue  # the corrupt and not-a-PDF files
        if pdf.needs_pass:
            pdf.authenticate("sample-user")
        text = "".join(page.get_text() for page in pdf)
        pdf.close()
        for ssn in re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text):
            assert ssn.startswith("900-00-"), path.name
        for ein in re.findall(r"\b\d{2}-\d{7}\b", text):
            assert ein.startswith("00-"), path.name


def test_the_set_is_the_same_every_time(sample_set: tuple[Path, dict], tmp_path: Path) -> None:
    assert generate(tmp_path) == sample_set[1]


def test_todays_reader_on_the_text_documents(sample_set: tuple[Path, dict]) -> None:
    """With the issuers known, today's reader gets type, year and issuer right on every text
    document of a type it has, fiscal-year K-1s included."""
    out, expected = sample_set
    result = score(out, with_institutions=True)
    countable = [
        a
        for a in expected["documents"]
        if a["kind"] == "text" and a["doc_type"] in BUILTIN_DOC_TYPES
    ]
    text = result["groups"]["text"]
    held_out = result["groups"]["text (held-out layout)"]
    assert text["all three"] + held_out["all three"] == len(countable)
