"""Help on each page: a few plain sentences per page, shown under "Help" at the top
of the page, with links to the privacy statement and About on the Help page (`/help`).

Plain words, no jargon the preparer normally handles, and never tax advice."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from taxbinder.demo import root as demo_root
from taxbinder.entities import entities_dir
from taxbinder.store.keys import SERVICE

# Where macOS keeps the app window's web data (institution websites' sign-ins and cookies):
# named after the app's identity, outside the app's own folder.
WEB_DATA = (
    "~/Library/WebKit/com.4ampersand.taxbinder",
    "~/Library/Caches/com.4ampersand.taxbinder",
)

PAGES: dict[str, list[str]] = {
    "start.html": [
        "TaxBinder keeps your household's tax documents in one place on this Mac and shows "
        "what's still missing. It doesn't give tax advice and doesn't prepare returns.",
        "Get started and add your documents. Last year's tax return, if you add it, tells "
        "TaxBinder who's in the household and which forms to expect. Or try the demo first, "
        "with an invented family.",
    ],
    "add.html": [
        "Drop PDFs, scans or photos of your tax documents, from any year, or a whole folder. "
        "TaxBinder reads each one on this Mac, works out what it is and who sent it, and files "
        "it. An exact copy of a file already here is skipped.",
        "Nothing is filed unseen: anything TaxBinder isn't sure of waits for Check.",
    ],
    "check.html": [
        "Check shows what TaxBinder found in the documents you added, and asks only what it "
        "can't be sure of: whose a document is, what kind it is, or which year it's for.",
        "View shows a document's pages, to compare with what was read. Your answers can be "
        "undone from Changes in your binder.",
    ],
    "backup.html": [
        "Your binder is encrypted. The recovery key opens it on another Mac, or on this one if "
        "its key is lost. Keep it somewhere safe, away from the Mac.",
        "A backup is one encrypted file with every binder, its documents and its history. It's "
        "safe to keep in iCloud, Google Drive or on an external drive.",
    ],
    "home.html": [
        "Your binder as a deck of cards, one for each year, the year you're working on in front. "
        "Click a year's tab to bring its card forward, and a square to see what it is, view "
        "the document, type its amount or a comment, or mark it not expected this year (with a "
        "short reason, so you remember why next year). Add to this year's list adds a document "
        "still to come that the binder doesn't know yet. Beside the deck: Hand off, what's still "
        "open, the checks, and what TaxBinder noticed.",
        "Start next year from this one at the top, or start an earlier year to file its "
        "documents: each document goes into the tax year printed on it.",
    ],
    "years.html": [
        "One row per document, one column per year: the amount in the row's box, read from each "
        "year's document. Click an amount to type one, or choose another box from the row's "
        "list. A big change from the year before is marked as a fact, not a problem.",
        "An amount you type is never replaced by one read from a document; if they differ, both "
        "show. An amount marked “to check” needs a look: ✓ keeps it, ✕ leaves the box empty. "
        "Every change can be undone from Changes.",
    ],
    "preparer.html": [
        "One .zip with your year's documents, sorted, and a summary for your preparer. You can "
        "make it any time and again when more arrives; it says when it's out of date.",
        "The .zip isn't encrypted, and TaxBinder never sends it: you give it to your preparer.",
    ],
    "documents.html": [
        "Every file in your binder. Click a file's name to see it, change what it is or which "
        "year it's for, or save copies to Downloads.",
    ],
    "payments.html": [
        "Tax payments you made or plan to make (estimated tax, property tax, vehicle fees), "
        "with the confirmation that shows each was paid. A scheduled payment doesn't count as "
        "paid until it is.",
    ],
    "donations.html": [
        "Gifts to charity for the year. A gift of $250 or more needs the charity's letter; a "
        "gift of stock or property also needs the transfer records.",
    ],
    "foreign.html": [
        "Accounts abroad for the year, for the FBAR: each one's highest balance in its own "
        "currency and the year-end exchange rate. TaxBinder adds them up per person against "
        "the $10,000 line and hands the statements and a summary to your preparer.",
    ],
    "history.html": [
        "Every change made in your binder, newest first. Undo puts a change's records back as "
        "they were before it.",
    ],
    "settings.html": [
        "How TaxBinder works on this Mac: other filers, how websites open, the inbox folder and "
        "the usual box for each form. Changes take effect straight away. To make the text "
        "larger, use View › Make Text Bigger (⌘+).",
    ],
    "privacy.html": [
        "Names and other values to replace when you copy a document's text for an AI chat. "
        "Values already in your binder (names, account numbers) are replaced without being "
        "listed here.",
    ],
    "sharing.html": [
        "Open your binder from a phone or another computer on your home network. Off until you "
        "turn it on; only devices you pair with a code can connect, over an encrypted link.",
    ],
    "help.html": [
        "What's stored where, what goes over the network, and the licences.",
    ],
    "licences.html": [
        "The free software libraries TaxBinder includes, each with the licence it's under. The "
        "list was made when this copy of TaxBinder was built.",
    ],
}


def for_page(name: str) -> list[str]:
    return PAGES.get(name, [])


@dataclass
class Places:
    """Where TaxBinder keeps things on this Mac (the help topics)."""

    folder: str  # the app's folder: the binder, other filers, the demo, two small notes
    binder: str
    others: str
    demo: str
    keychain: str
    web: tuple[str, ...]
    models: str  # the reading models: beside the binder, never in it


def places(vault: Path) -> Places:
    def short(p: Path) -> str:
        home = str(Path.home())
        s = str(p.expanduser())
        return "~" + s[len(home) :] if s.startswith(home + "/") else s

    from taxbinder.llm import folder

    return Places(short(vault.parent), short(vault), short(entities_dir(vault)),
                  short(demo_root(vault)), SERVICE, WEB_DATA, short(folder(vault)))  # fmt: skip


LICENCES = "THIRD-PARTY-LICENSES.txt"


def licences_file() -> Path | None:
    """The third-party licences, made when the app was built: in the app (frozen), else
    in the repository's build folder; None in a copy built without it."""
    if env := os.environ.get("TAXBINDER_LICENCES"):
        path = Path(env)
    elif bundled := getattr(sys, "_MEIPASS", None):
        path = Path(bundled) / "licences" / LICENCES
    else:
        path = Path(__file__).resolve().parents[2] / "build" / "licences" / LICENCES
    return path if path.is_file() else None


def licence_count(path: Path) -> int:
    """How many libraries the file lists (its "…: N" group lines, before the texts)."""
    with path.open() as f:
        head = f.read(4096)
    return sum(int(n) for n in re.findall(r"^In the .*: (\d+)$", head, re.M))
