"""A newer TaxBinder (updates.py): the version read from GitHub's redirect, once a day, and
the notice on Settings and Your binder. Nothing here goes over the network."""

from __future__ import annotations

import urllib.error
from email.message import Message
from importlib.metadata import version
from pathlib import Path

import pytest
from test_settings import app, store  # noqa: F401 - the fixture
from test_web import ORIGIN

from taxbinder import updates
from taxbinder.store import RecordStore
from taxbinder.updates import Updates


def redirect(location: str, code: int = 302):  # noqa: ANN201
    """A stand-in for GitHub: every request answered with a redirect to `location`."""
    asked: list[str] = []

    def answer(req, timeout):  # noqa: ANN001, ANN202
        asked.append(req.full_url)
        headers = Message()
        headers["Location"] = location
        raise urllib.error.HTTPError(req.full_url, code, "Found", headers, None)

    answer.asked = asked
    return answer


def offline(req, timeout):  # noqa: ANN001, ANN201
    raise urllib.error.URLError("no connection")


def test_versions_compare_as_numbers() -> None:
    assert updates.version_key("0.10.1") == (0, 10, 1)
    assert updates.version_key("v0.10") is None and updates.version_key("0.10.0-beta") is None
    u = Updates(newest="0.11.0")
    assert u.newer("0.10.0") == "0.11.0" and u.newer("0.9.9") == "0.11.0"
    assert u.newer("0.11.0") is None and u.newer("0.12.0") is None
    assert Updates(newest="0.10.10").newer("0.10.9") == "0.10.10"  # not compared as text
    assert Updates().newer("0.10.0") is None


def test_the_version_is_read_from_the_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    gh = redirect("https://github.com/4ampersand/taxbinder/releases/tag/v0.11.0")
    monkeypatch.setattr(updates, "_open", gh)
    assert updates.newest() == "0.11.0" and gh.asked == [updates.LATEST]
    monkeypatch.setattr(updates, "_open", redirect("/4ampersand/taxbinder/releases/tag/v0.12.1"))
    assert updates.newest() == "0.12.1"  # an address on github.com itself
    for elsewhere in ("https://example.com/releases/tag/v9.0.0",
                      "http://github.com/4ampersand/taxbinder/releases/tag/v9.0.0",
                      "https://github.com/4ampersand/taxbinder/releases/tag/nightly"):  # fmt: skip
        monkeypatch.setattr(updates, "_open", redirect(elsewhere))
        assert updates.newest() is None
    monkeypatch.setattr(updates, "_open", redirect("", 404))
    with pytest.raises(OSError):  # not a redirect: an error, kept quiet by the callers
        updates.newest()


def test_at_launch_once_a_day_and_never_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh = redirect("https://github.com/4ampersand/taxbinder/releases/tag/v0.11.0")
    monkeypatch.setattr(updates, "_open", gh)
    updates.at_launch(tmp_path, now=1_000_000)
    assert Updates.load(tmp_path) == Updates(True, 1_000_000, "0.11.0")
    updates.at_launch(tmp_path, now=1_000_000 + updates.DAY - 1)
    assert len(gh.asked) == 1  # not again the same day
    updates.at_launch(tmp_path, now=1_000_000 + updates.DAY)
    assert len(gh.asked) == 2
    Updates(check=False).save(tmp_path)
    updates.at_launch(tmp_path, now=9_000_000)
    assert len(gh.asked) == 2  # turned off: GitHub isn't asked


def test_offline_is_quiet_and_tried_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Updates(newest="0.11.0", checked=1).save(tmp_path)
    monkeypatch.setattr(updates, "_open", offline)
    updates.at_launch(tmp_path, now=1_000_000)  # nothing raised
    assert Updates.load(tmp_path) == Updates(True, 1, "0.11.0")  # still due next launch


def test_a_broken_file_is_the_defaults(tmp_path: Path) -> None:
    (tmp_path / "updates.json").write_text('{"check": false, "newest": "<b>9</b>"}')
    assert Updates.load(tmp_path) == Updates(check=False)  # never shown: not a version
    (tmp_path / "updates.json").write_text("not json")
    assert Updates.load(tmp_path) == Updates()


def test_settings_and_home_say_a_newer_version_is_out(store: RecordStore) -> None:  # noqa: F811
    state, c = app(store)
    r = c.get("/settings")
    assert "Check for updates" in r.text and "is available" not in r.text
    assert "This is the latest version" not in r.text  # never checked yet
    Updates(checked=1, newest=version("taxbinder")).save(state.updates_dir)
    assert "This is the latest version" in c.get("/settings").text
    Updates(checked=1, newest="99.0.0").save(state.updates_dir)
    assert "TaxBinder 99.0.0 is available" in c.get("/settings").text
    assert "TaxBinder 99.0.0 is available" in c.get("/").text


def test_the_settings_controls(store: RecordStore, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    opened: list[str] = []
    state, c = app(store, open_url=opened.append)
    assert c.post("/api/updates", json={"check": "off"}, headers=ORIGIN).json()["check"] is False
    assert Updates.load(state.updates_dir).check is False
    assert c.post("/api/updates", json={"check": "maybe"}, headers=ORIGIN).status_code == 400
    monkeypatch.setattr(updates, "_open", offline)
    r = c.post("/api/updates", json={"now": True}, headers=ORIGIN)
    assert r.status_code == 400 and "couldn't reach GitHub" in r.json()["error"]
    monkeypatch.setattr(
        updates, "_open", redirect("https://github.com/4ampersand/taxbinder/releases/tag/v99.0.0")
    )
    assert c.post("/api/updates", json={"now": True}, headers=ORIGIN).json()["newer"] == "99.0.0"
    assert Updates.load(state.updates_dir).check is False  # Check now leaves the choice as it was
    assert c.post("/api/updates/download", json={}, headers=ORIGIN).status_code == 202
    assert opened == [updates.LATEST]  # the release page, in the browser
