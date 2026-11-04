"""Document types added by the person: definition, recognition, matching, package, app, CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from pdfgen import make_pdf
from test_web import ORIGIN, _upload, client, state  # noqa: F401 - `state` is a fixture
from test_years import store  # noqa: F401 - fixture

from taxbinder.doctypes import add_doc_type, is_known, labels, type_id
from taxbinder.ingest.inbox import ingest_file
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState
from taxbinder.years import add_row

PDF = make_pdf(
    ["Form 592-B 2025", "Resident and Nonresident Withholding Tax Statement", "Harbor LP"]
)


def test_add_doc_type(store: RecordStore) -> None:  # noqa: F811
    d = add_doc_type(store, " Form 592-B ", "03-K1", ["Form 592-B", " "], "t")
    assert (d.id, d.name, d.folder, d.keywords) == (
        "x-form-592-b",
        "Form 592-B",
        "03-K1",
        ["Form 592-B"],
    )
    assert add_doc_type(store, "form 592-b", "09-Other", [], "t") == d  # same id → the existing one
    assert is_known(store, "x-form-592-b") and is_known(store, "k1_1065")
    assert not is_known(store, "x-nope") and not is_known(store, "nonsense")
    assert labels(store)["x-form-592-b"] == "Form 592-B"
    with pytest.raises(ValueError, match="built-in"):
        add_doc_type(store, "K1", "03-K1", [], "t")
    with pytest.raises(ValueError, match="letters or digits"):
        type_id("!!!")
    with pytest.raises(ValueError):
        add_doc_type(store, "Insurance renewal", "10-Nowhere", [], "t")  # not a package folder
    with pytest.raises(ValueError, match="unknown document type"):
        add_row(store, 2025, "Harbor", "LP", "x-not-added", "primary", "t")  # type: ignore[arg-type]


def test_added_type_is_recognised_matched_and_packaged(store: RecordStore, tmp_path: Path) -> None:  # noqa: F811
    from taxbinder.package.build import build_package

    add_doc_type(store, "Form 592-B", "03-K1", ["Form 592-B"], "t")
    row = add_row(store, 2025, "Harbor Capital", "Harbor LP", "x-form-592-b", "primary", "t")  # type: ignore[arg-type]
    path = store.vault.inbox / "harbor-withholding.pdf"
    path.write_bytes(PDF)
    result = ingest_file(store, path, "upload", "t")
    doc, _ = store.get("document", result.doc_id)
    assert doc.classification.doc_type == "x-form-592-b"  # the person's words win over built-ins
    assert result.linked_to == row
    out = build_package(store, 2025, tmp_path / "pkg", "t").root
    files = [p.relative_to(out).as_posix() for p in out.rglob("*.pdf")]
    assert "03-K1/Harbor Capital - Harbor LP - Form 592-B.pdf" in files


def test_new_type_from_the_add_row_dialog(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/").text  # Expect another document, on the deck card
    assert 'value="__new__">＋ A new kind of document…' in html and 'id="nt-folder"' in html
    r = c.post(
        "/api/doc-types",
        json={
            "name": "Insurance renewal",
            "folder": "09-Other",
            "keywords": "Policy renewal, Declarations page",
        },
        headers=ORIGIN,
    )
    assert r.status_code == 200 and {k: v for k, v in r.json().items() if k != "undo"} == {
        "id": "x-insurance-renewal",
        "name": "Insurance renewal",
        "folder": "09-Other",
        "keywords": ["Policy renewal", "Declarations page"],
    }
    bad = c.post("/api/doc-types", json={"name": "W-2"}, headers=ORIGIN)
    assert bad.status_code == 400 and "built-in" in bad.json()["error"]
    row = {
        "year": 2025,
        "institution": "Acme Insurance",
        "source": "Home policy",
        "doc_type": "x-insurance-renewal",
        "owner": "primary",
    }
    assert (
        c.post("/api/rows", json=row, headers=ORIGIN).json()["id"]
        == "2025/acme-insurance.home-policy.x-insurance-renewal"
    )
    html = c.get("/").text
    assert '<optgroup label="Yours"><option value="x-insurance-renewal">Insurance renewal' in html
    assert 'data-kind="Insurance renewal"' in html
    unknown = c.post("/api/rows", json={**row, "doc_type": "x-ghost"}, headers=ORIGIN)
    assert unknown.status_code == 400 and "unknown document type" in unknown.json()["error"]


def test_doctype_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_cli import cli

    monkeypatch.setenv("TAXBINDER_VAULT", str(tmp_path / "vault"))
    cli("init")
    assert "x-form-592-b: Form 592-B → 03-K1" in cli(
        "doctype", "add", "Form 592-B", "--folder", "03-K1", "--keyword", "592-B"
    )
    listing = cli("doctype", "list")
    assert "k1_1065" in listing and "x-form-592-b" in listing and "592-B" in listing
