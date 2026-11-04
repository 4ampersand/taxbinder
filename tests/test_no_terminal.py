"""No Terminal in the app: every message the app can show offers an action in the
app, never a command to run. A busy binder → Try again; another filer's missing key → Unlock with
the recovery key; and no page, script or app-side message names a command."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from test_safe import _vault_id
from test_start import _household, _state
from test_web import ORIGIN, client

import taxbinder
from taxbinder.entities import list_entities
from taxbinder.store import RecordStore, keys_from_env, open_vault

SRC = Path(taxbinder.__file__).parent
COMMAND = re.compile(r"`taxbinder\b|\btaxbinder (?:init|restore-key|reconcile|fsck|key|backup)\b"
                     r"|\buv run\b|\bmake (?:test|dev|privacy|samples)\b|\bTerminal\b")  # fmt: skip
# The command line and the developer tools are for people who use commands.
FOR_COMMANDS = {"cli.py", "sidecar.py"}


def _strings(path: Path) -> list[str]:
    """A module's string constants, without its docstrings (those are for developers)."""
    tree = ast.parse(path.read_text())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}  # fmt: skip
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]  # fmt: skip


def test_no_page_or_message_names_a_command() -> None:
    found = []
    for path in SRC.rglob("*"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "dev" or path.name in FOR_COMMANDS:
            continue
        if path.suffix in (".html", ".js"):
            texts = [re.sub(r"\{#.*?#\}|//[^\n]*", "", path.read_text(), flags=re.S)]
        elif path.suffix == ".py":
            texts = _strings(path)
        else:
            continue
        found += [f"{rel}: {m.group()}" for t in texts for m in COMMAND.finditer(t)]
    assert found == []


def test_a_busy_binder_and_try_again(tmp_path: Path) -> None:
    vault, _ = _household(tmp_path)
    holder = RecordStore(open_vault(vault, keys_from_env()))  # something else has it open
    state = _state(vault, tmp_path)
    c = client(state)
    page = c.get("/").text
    assert "open in another copy of TaxBinder" in page and 'id="retry-go"' in page
    assert c.post("/api/start/retry", headers=ORIGIN).status_code == 500  # still busy
    holder.close()
    r = c.post("/api/start/retry", headers=ORIGIN)
    assert r.status_code == 200 and state.store is not None


def test_another_filers_missing_key_asks_for_the_recovery_key(tmp_path: Path) -> None:
    vault, recovery = _household(tmp_path)
    trust = list_entities(vault)[1]
    keys_from_env().delete(_vault_id(trust.path))  # this filer's key only is gone
    state = _state(vault, tmp_path)
    c = client(state)
    home = c.get("/").text
    assert "Locked: needs your recovery key" in home  # still listed, not hidden
    assert c.post("/api/entity", json={"slug": trust.slug}, headers=ORIGIN).status_code == 200
    page = c.get("/").text
    assert "Unlock your binder" in page and 'id="unlock-key"' in page
    assert "restore-key" not in page and "Quit TaxBinder" not in page
    r = c.post("/api/start/unlock", json={"key": recovery}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert "Locked" not in c.get("/").text and keys_from_env().get(_vault_id(trust.path))
