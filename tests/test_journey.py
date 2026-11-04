"""The journey's steps, behind your binder's home: the top bar's Check with its
badge, the page you're on, and the app opening on the next thing to do."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from helpers import new_vault
from pdfgen import make_pdf
from quills import build
from test_web import ORIGIN, client

from taxbinder import journey
from taxbinder.ingest.inbox import ingest_file
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState

TODAY = date(2026, 3, 2)


def _app(store: RecordStore, tmp_path: Path) -> AppState:
    s = AppState(vault_path=store.vault.root, store=store, port=8765, emit=lambda e: None,
                 today=lambda: TODAY)  # fmt: skip
    s.downloads_dir = tmp_path / "downloads"
    return s


def _menu(html: str) -> dict[str, str]:
    """Link label → "on" for the page you're on, else "", and its badge, in the top bar."""
    menu = html.split('<nav class="topnav-links"')[1].split("</nav>")[0]
    out = {}
    for cls, body in re.findall(r'<a href="[^"]*" class="([^"]*)"[^>]*>(.*?)</a>', menu, re.S):
        label = re.match(r"\s*([^<]+)", body).group(1).strip()
        badge = re.search(r"<small[^>]*>([^<]+)</small>", body)
        out[label] = cls + (f" · {badge.group(1)}" if badge else "")
    return out


@pytest.fixture
def empty(tmp_path: Path):
    s = RecordStore(new_vault(tmp_path / "vault"))
    yield s
    s.close()


def test_a_new_vault_starts_at_add_documents(empty: RecordStore, tmp_path: Path) -> None:
    steps = {s.id: s for s in journey.steps(empty, 2025, empty.vault.root, TODAY)}
    assert list(steps) == ["start", "add", "check", "safe", "results", "handoff"]
    assert steps["start"].done and not steps["add"].done and not steps["safe"].done
    assert steps["check"].badge == "1 question"  # the household, until confirmed
    assert journey.next_step(list(steps.values())).id == "add"
    state = _app(empty, tmp_path)
    c = client(state, login=False)
    r = c.get(f"/bootstrap?t={state.bootstrap_code}", follow_redirects=False)
    assert 'content="0;url=/"' in r.text  # the app opens on Your binder
    html = c.get("/documents").text
    assert "Last backup: never" in html and 'class="gear' in html


def test_the_steps_follow_the_season(empty: RecordStore, tmp_path: Path) -> None:
    from taxbinder import backup

    build(empty, "test")
    pdf = empty.vault.inbox / "k1.pdf"
    pdf.write_bytes(
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    )
    ingest_file(empty, pdf, "upload", "test")
    steps = {s.id: s for s in journey.steps(empty, 2025, empty.vault.root, TODAY)}
    assert steps["add"].done and re.fullmatch(r"\d+ to come", steps["add"].badge)
    assert not steps["results"].done  # documents still to come
    backup.create(empty.vault, tmp_path / "out")
    steps = journey.steps(empty, 2025, empty.vault.root, TODAY)
    assert next(s for s in steps if s.id == "safe").done
    # Keep it safe isn't shown in later years once it's done, nor over home sharing.
    from taxbinder.years import new_year

    new_year(empty, 2026, 2025, "test")
    assert "safe" not in {s.id for s in journey.steps(empty, 2026, empty.vault.root, TODAY)}
    assert "safe" in {s.id for s in journey.steps(empty, 2025, empty.vault.root, TODAY)}
    assert "safe" not in {s.id for s in journey.steps(empty, 2025, empty.vault.root, TODAY, True)}


def test_every_page_shows_where_you_are(empty: RecordStore, tmp_path: Path) -> None:
    build(empty, "test")
    pdf = empty.vault.inbox / "k1.pdf"
    pdf.write_bytes(
        make_pdf(["Schedule K-1 (Form 1065) 2025", "Fernhollow properties", "Kettle 410"])
    )
    ingest_file(empty, pdf, "upload", "test")
    c = client(_app(empty, tmp_path))
    here = {"/": "Your binder", "/check": "Check",
            "/documents": "Documents", "/payments": "Documents"}  # fmt: skip
    for path, link in here.items():
        menu = _menu(c.get(path).text)
        assert [k for k, v in menu.items() if v.startswith("on")] == [link], (path, menu)
    # no Add documents in the bar: Your binder, Documents and Check have it
    assert list(_menu(c.get("/add").text)) == ["Your binder", "Check", "Documents",
                                                    "Across the years"]  # fmt: skip
    assert "/add" in c.get("/").text and "/add" in c.get("/documents").text
    help_, settings = c.get("/help").text, c.get("/settings").text  # the demo: in Help
    assert 'id="demo"' in help_ and "demo-start" in help_ and "demo-start" not in settings
    for path in ("/backup", "/preparer"):
        assert c.get(path).status_code == 200  # reached from a year's card
    html = c.get("/documents").text
    assert 'class="binder-tabs"' in html and ">Changes</a>" in html
    assert '<ol class="journey"' not in html and 'id="year-pick"' not in html  # no side menu
    check = c.get("/check?year=2025").text
    assert "Here's what we found" in check and "Nothing is waiting for Check" in check
    assert 'id="household"' not in check  # filed before, household confirmed: not asked again
    assert "Make the .zip" in c.get("/preparer").text


def test_undo_is_offered_after_a_change(empty: RecordStore, tmp_path: Path) -> None:
    build(empty, "test")
    c = client(_app(empty, tmp_path))
    r = c.post("/api/donations", json={"payee": "Heron Trust", "date": "2025-10-01", "amount": "60"},
               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200 and r.json()["undo"]["sha"]  # the page's toast offers it
