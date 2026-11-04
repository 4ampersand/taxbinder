"""Files for your preparer: one .zip under one top folder, with the summary,
the documents by type, payments, donations and what's still to come; remembered when made, out
of date once documents arrive after it; never sent (the invented household; PDFs made here)."""

from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pdfplumber
import pytest
from pdfgen import make_pdf
from test_package import NOW, store  # noqa: F401 - `store` is a fixture
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder import journey
from taxbinder.ingest.inbox import ingest_file
from taxbinder.package import handoff
from taxbinder.store import RecordStore
from taxbinder.web.app import LOCAL_ONLY, AppState

TOP = "TaxBinder 2025 for your preparer"


def _add(store: RecordStore, name: str, lines: list[str]) -> None:  # noqa: F811
    path = store.vault.inbox / name
    path.write_bytes(make_pdf(lines))
    ingest_file(store, path, "upload", now=NOW)


def test_one_zip_with_everything_for_the_preparer(
    store: RecordStore,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    sent = {e.id: e.delivery for e in store.list("expectation", 2025)}
    assert handoff.last(store, 2025) is None

    path, result = handoff.make(store, 2025, tmp_path / "Downloads", "test", now=NOW)
    assert path.name == f"{TOP}.zip" and sum(result.files.values()) == 5  # amended K-1: 1
    assert list(scratch.iterdir()) == []  # the decrypted folder is gone
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert all(n.startswith(f"{TOP}/") for n in names)
        for want in ("00-summary.pdf", "index.csv", "open-items.csv", "01-W2/",
                     "05-Estimated-Payments/schedule.csv", "06-Donations/donations.csv"):  # fmt: skip
            assert f"{TOP}/{want}" in names
        listed = z.read(f"{TOP}/06-Donations/donations.csv").decode()
        gifts = list(csv.DictReader(io.StringIO(listed)))
        with pdfplumber.open(io.BytesIO(z.read(f"{TOP}/00-summary.pdf"))) as pdf:
            summary = "\n".join(p.extract_text() or "" for p in pdf.pages)
    wrens = next(g for g in gifts if g["charity"] == "Friends of the Marsh Wrens")
    assert wrens["letter linked"] == "yes" and all(g["letter linked"] == "no" for g in gifts
                                                   if g is not wrens)  # fmt: skip
    assert "Still to come" in summary and "aren't encrypted" in summary
    assert "Linden Education, Jordan, W-2" not in summary  # here: not to come
    to_come = summary.split("Still to come")[1].split("Most important")[0]
    assert "- Copperfield Collective, Ashcombe Partners, K1" in to_come

    made = handoff.last(store, 2025)
    assert made is not None and made.documents == 5 and made.new == 0
    # making it never marks anything sent; one filer, one year
    assert {e.id: e.delivery for e in store.list("expectation", 2025)} == sent
    again, _ = handoff.make(store, 2025, tmp_path / "Downloads", "test", now=NOW)
    assert again.name == f"{TOP} (2).zip" and path.exists()  # never overwritten


def test_out_of_date_when_documents_arrive_after_it(
    store: RecordStore,  # noqa: F811
    tmp_path: Path,
) -> None:
    def step() -> journey.Step:
        steps = journey.steps(store, 2025, tmp_path / "vault", date(2026, 3, 1))
        return next(s for s in steps if s.id == "handoff")

    assert not step().done and step().badge == ""
    handoff.make(store, 2025, tmp_path / "Downloads", "test", now=NOW)
    assert step().done and step().badge == ""
    _add(store, "2024 1099.pdf", ["Brackley Wealth", "2024 Form 1099-INT", "Interest"])
    assert handoff.last(store, 2025).new == 0  # another year's document: not this .zip's
    _add(store, "late w2.pdf", ["Form W-2 Wage and Tax Statement 2025", "Linden Education", "Ari"])
    assert handoff.last(store, 2025).new == 1
    assert not step().done and step().badge == "out of date" and step().news


def test_the_page(state: AppState, tmp_path: Path) -> None:  # noqa: F811
    state.downloads_dir = tmp_path / "Downloads"
    c = client(state)
    html = c.get("/preparer").text
    assert "Make the .zip" in html and "isn't locked" in html and "never sends it" in html
    assert "Not yet" in html  # Results' answer: documents still to come
    r = c.post("/api/preparer/save", json={"year": 2025}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert (state.downloads_dir / r.json()["name"]).exists()
    html = c.get("/preparer").text
    assert "Make it again" in html and "nothing new since" in html
    assert "/api/preparer/save" in LOCAL_ONLY  # a paired phone can't make it on this Mac
