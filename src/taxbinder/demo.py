"""Try with sample data: a demo binder for the Quills, an invented household.

It lives in its own folder beside the person's vault (`demo/`), locked with a key file there
(never the Keychain), and is set up by the app's own steps from last year's sample return:
read it, say it's the filed copy, confirm the household it names, file it on Check. This year's
sample documents (forms, a scan, a photo, a damaged file) are then added by the person.

The sample files come with the app, made when it's built (`tests/samples/demo.py`); the app
never makes them (PyMuPDF is for development only)."""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date
from pathlib import Path

from taxbinder import check, returns
from taxbinder import household as home
from taxbinder.ingest.inbox import ingest_file
from taxbinder.store import FileKeys, KeyStore, RecordStore, init_vault
from taxbinder.store import keys as store_keys

ACTOR = "demo"
RETURN_YEAR = 2024  # the sample return's year; the demo's year is the one after
# The demo's own today, mid-season for its year: documents still arriving, the deadline weeks
# away, the same whenever it's opened (you chose a fixed date).
TODAY = date(RETURN_YEAR + 2, 3, 1)


def root(home_vault: Path) -> Path:
    """The demo's folder, beside the person's vault (never inside it)."""
    return home_vault.expanduser().parent / "demo"


def vault_path(home_vault: Path) -> Path:
    return root(home_vault) / "vault"


def keys(home_vault: Path) -> KeyStore:
    return FileKeys(root(home_vault) / "demo-key.json")  # invented data: a key file will do


def samples() -> Path:
    """Where the sample files are: in the app (frozen), else built by `make samples`."""
    if env := os.environ.get("TAXBINDER_SAMPLES"):
        return Path(env)
    if bundled := getattr(sys, "_MEIPASS", None):
        return Path(bundled) / "demo-samples"
    return Path(__file__).resolve().parents[2] / "build" / "demo-samples"


def documents() -> list[Path]:
    """This year's sample documents, to add (or save to a folder)."""
    folder = samples() / "documents"
    if not folder.is_dir():
        raise FileNotFoundError("the sample documents aren't in this copy of TaxBinder")
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def ready(home_vault: Path) -> bool:
    return (root(home_vault) / "ready").exists()


def create(home_vault: Path) -> None:
    """A new demo binder, set up from last year's sample return as a person would on Check.
    Anything there before goes; if setting up fails, nothing is left half-made."""
    (ret,) = sorted((samples() / "return").glob("*.pdf"))
    remove(home_vault)
    demo_keys = keys(home_vault)
    store_keys.use(demo_keys)  # anything opened while setting up uses the demo's key file
    try:
        _set_up(home_vault, demo_keys, ret)
    except BaseException:
        remove(home_vault)
        raise
    finally:
        store_keys.use(None)
    (root(home_vault) / "ready").touch()


def _set_up(home_vault: Path, demo_keys: KeyStore, ret: Path) -> None:
    vault, _ = init_vault(vault_path(home_vault), demo_keys)  # no recovery key: invented data
    with RecordStore(vault) as store:
        inbox = vault.inbox / f"{RETURN_YEAR} tax return.pdf"
        shutil.copyfile(ret, inbox)
        doc = ingest_file(store, inbox, "demo", ACTOR, waiting=True).doc_id
        returns.set_details(store, doc, ACTOR, status="filed")
        house = home.worked_out(store, RETURN_YEAR + 1)
        home.confirm(store, house, RETURN_YEAR + 1, ACTOR)
        check.confirm(store, ACTOR)


def remove(home_vault: Path) -> None:
    """Start over: the demo's folder goes; the person's vault is never touched."""
    if (folder := root(home_vault)).is_dir():
        shutil.rmtree(folder)
