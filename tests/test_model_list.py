"""Signing the list of reading models (`taxbinder.dev.model_list`), with a stand-in Keychain:
the real one is never touched."""

from __future__ import annotations

import json
from pathlib import Path

import keyring
import pytest

from taxbinder import llm
from taxbinder.dev import model_list


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict:
    kept: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, a: kept.get((s, a)))
    monkeypatch.setattr(keyring, "set_password", lambda s, a, p: kept.__setitem__((s, a), p))
    return kept


def test_keygen_then_sign_makes_a_list_the_app_takes(keychain: dict, tmp_path: Path) -> None:
    public = model_list.keygen()
    assert list(keychain) == [(model_list.SERVICE, model_list.ACCOUNT)]
    assert public not in keychain.values()  # only the private key is kept
    with pytest.raises(SystemExit, match="not replaced"):
        model_list.keygen()

    path = tmp_path / "reading_models.json"
    path.write_bytes(llm.BUILT_IN.read_bytes())
    sig = model_list.sign(path)
    assert sig.name == "reading_models.json.sig"
    assert llm.verify(path.read_bytes(), sig.read_bytes(), public)
    assert not llm.verify(path.read_bytes() + b" ", sig.read_bytes(), public)


def test_never_signs_without_a_key_or_a_list_the_app_cant_use(
    keychain: dict, tmp_path: Path
) -> None:
    path = tmp_path / "list.json"
    path.write_text(json.dumps({"version": 1, "models": [{"id": "x"}]}))
    with pytest.raises(SystemExit, match="run keygen first"):
        model_list.sign(path)
    model_list.keygen()
    with pytest.raises(ValueError, match="not an AI model list"):
        model_list.sign(path)
    assert not (tmp_path / "list.json.sig").exists()
