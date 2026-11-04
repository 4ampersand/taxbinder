"""Keep it safe: the recovery key is shown once, only at this Mac, and confirmed by
its last 4 characters; the step is done with the key saved and a backup made; "Remind me later"
puts it off for a week (fictional data)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_start import _new_mac, _state
from test_web import ORIGIN, client

from taxbinder import journey, safe, start
from taxbinder.store import init_vault, keys_from_env


def _started(tmp_path: Path):  # noqa: ANN202
    """A binder made by Get started, its recovery key waiting in the Keychain."""
    state = _state(tmp_path / "app" / "vault", tmp_path)
    c = client(state)
    assert c.post("/api/start", headers=ORIGIN).status_code == 200
    return state, c


def _step(state) -> journey.Step:  # noqa: ANN001
    steps = journey.steps(state.store, 2025, state.vault_path, state.today())
    return next(s for s in steps if s.id == "safe")


def test_the_key_is_shown_once_and_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    state, c = _started(tmp_path)
    key = safe.pending(state.vault_path)
    assert key and key.startswith("AGE-SECRET-KEY-")
    page = c.get("/backup").text
    assert "Show my recovery key" in page and key not in page  # only on a click
    r = c.post("/api/safe/key", json={}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["key"] == key
    assert r.headers["cache-control"] == "no-store"

    for wrong in ("", "abc", key[-5:-1], "zzzz"):
        assert c.post("/api/safe/confirm", json={"last4": wrong}, headers=ORIGIN).status_code == 400
    assert safe.pending(state.vault_path) == key  # nothing changed
    r = c.post("/api/safe/confirm", json={"last4": key[-4:].lower()}, headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"saved": True, "done": False}  # no backup yet
    assert safe.pending(state.vault_path) is None
    assert keys_from_env().get(start.pending_recovery_id(state.store.vault)) is None
    assert c.post("/api/safe/key", json={}, headers=ORIGIN).status_code == 400  # gone for good
    assert "Saved." in c.get("/backup").text
    assert key not in caplog.text and key[-12:] not in caplog.text

    # The key it showed opens the binder: on a Mac without its key, Unlock with it works.
    path = state.vault_path
    state.store.close()
    _new_mac(monkeypatch)
    again = _state(path, tmp_path)
    assert again.problem_kind == "nokey"
    assert (
        client(again).post("/api/start/unlock", json={"key": key}, headers=ORIGIN).status_code
        == 200
    )
    again.store.close()


def test_done_with_the_key_saved_and_a_backup(tmp_path: Path) -> None:
    state, c = _started(tmp_path)
    assert c.post("/api/backup", headers=ORIGIN).status_code == 200
    assert not _step(state).done  # a backup alone isn't enough
    key = safe.pending(state.vault_path)
    c.post("/api/safe/confirm", json={"last4": key[-4:]}, headers=ORIGIN)
    assert _step(state).done and "Your binder is safe" in c.get("/backup").text
    assert "Remind me later" not in c.get("/backup").text
    state.store.close()


def test_remind_me_later_puts_it_off_for_a_week(tmp_path: Path) -> None:
    state, c = _started(tmp_path)
    today = state.today()
    # with documents added and checked, the next thing is Keep it safe ...
    steps = [
        journey.Step(i, i, "/" + i, done=i != "safe") for i in ("add", "check", "safe", "results")
    ]
    assert journey.next_step(steps).id == "safe"
    r = c.post("/api/safe/later", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["go"] == "/"
    assert safe.reminded(state.vault_path, today)
    assert journey.next_step(steps, later=True).id == "results"  # ... until a week has gone by
    assert not _step(state).done  # still unticked
    assert safe.reminded(state.vault_path, today + timedelta(days=6))
    assert not safe.reminded(state.vault_path, today + timedelta(days=7))
    assert not safe.reminded(tmp_path / "nowhere" / "vault", date(2026, 1, 1))
    state.store.close()


def test_a_binder_made_in_terminal_counts_as_saved(tmp_path: Path) -> None:
    vault, _ = init_vault(tmp_path / "app" / "vault", keys_from_env())  # its key printed then
    state = _state(vault.root, tmp_path)
    assert safe.saved(vault.root)
    c = client(state)
    page = c.get("/backup").text
    assert "Show this Mac's key instead" in page and "Show my recovery key" not in page
    assert c.post("/api/safe/key", json={}, headers=ORIGIN).status_code == 400
    r = c.post("/api/safe/key", json={"mac": True}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["key"] == keys_from_env().get(_vault_id(vault.root))
    state.store.close()


def _vault_id(path: Path) -> str:
    from taxbinder.store.vault import Vault
    from taxbinder.yamlio import load_yaml

    return load_yaml(Vault(path).info_file.read_text())["id"]


def test_keep_it_safe_is_this_macs_only() -> None:
    from taxbinder.web.app import LOCAL_ONLY

    for path in ("/backup", "/api/safe/key", "/api/safe/confirm", "/api/safe/later"):
        assert path in LOCAL_ONLY
