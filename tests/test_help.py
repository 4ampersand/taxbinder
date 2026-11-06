"""Help on each page, the privacy statement and About."""

from __future__ import annotations

import re
from pathlib import Path

from test_sharing import pair, shared  # noqa: F401 - `shared` is a fixture
from test_web import client, state  # noqa: F401 - `state` is a fixture

from taxbinder import help as helptext
from taxbinder.store.crypto import ENCRYPTED_FIELDS
from taxbinder.web.app import AppState

TEMPLATES = Path(helptext.__file__).parent / "web" / "templates"
# Each encrypted field, as the privacy statement names it
NAMED = {
    "number": "Account numbers",
    "account_number": "Account numbers",
    "routing_number": "routing numbers",
    "ssn": "Social Security numbers",
    "tin": "EINs",
    "issuer_tin": "EINs",
    "ein": "EINs",
    "confirmation_number": "confirmation numbers",
    "secret": "The values you list to hide",
}


def test_every_page_has_its_help(state: AppState) -> None:  # noqa: F811
    pages = sorted(p.name for p in TEMPLATES.glob("*.html")
                   if '{% extends "page.html" %}' in p.read_text())  # fmt: skip
    assert pages and [p for p in pages if not helptext.for_page(p)] == []
    c = client(state)
    for path in ("/", "/add", "/check", "/years", "/preparer", "/documents",
                 "/payments", "/donations", "/history", "/settings", "/sharing"):  # fmt: skip
        html = c.get(path).text
        assert 'class="help-pop"' in html and 'href="/help#privacy"' in html, path
        assert 'href="/help"' in html, path  # the side menu's foot


def test_the_privacy_statement_and_about(state: AppState) -> None:  # noqa: F811
    html = client(state).get("/help").text
    for part in ("Not tax advice", "What's stored where", "<h3>Encrypted</h3>",
                 "<h3>Not encrypted</h3>", "What goes over the network", "Home sharing",
                 "GNU Affero General Public License", "at most once a day, it asks GitHub"):  # fmt: skip
        assert part in html, part
    assert str(state.vault_path) in html and 'class="help-pop"' not in html
    assert set(NAMED) == ENCRYPTED_FIELDS  # a new encrypted field: say so in the statement
    encrypted = html.split("<h3>Encrypted</h3>")[1].split("<h3>Not encrypted</h3>")[0]
    for name in NAMED.values():
        assert name in encrypted, name


def test_never_nothing_is_sent_anywhere() -> None:
    texts = [p.read_text() for p in TEMPLATES.glob("*.html")]
    texts.append(Path(helptext.__file__).read_text())
    assert not any(re.search(r"nothing (is|gets) sent", t, re.IGNORECASE) for t in texts)


def test_a_paired_phone_sees_help(shared: AppState) -> None:  # noqa: F811
    html = pair(shared).get("/help").text
    assert "What's stored where" in html and str(shared.vault_path) not in html


def test_the_help_topics(state: AppState) -> None:  # noqa: F811
    from taxbinder.demo import root as demo_root
    from taxbinder.entities import entities_dir
    from taxbinder.store.keys import SERVICE

    html = client(state).get("/help").text
    for anchor in ("where", "backups", "new-mac", "uninstall"):
        assert f'id="{anchor}"' in html and f'href="#{anchor}"' in html, anchor
    where = helptext.places(state.vault_path)  # the paths the code uses
    assert where.others.endswith(entities_dir(state.vault_path).name)
    assert where.demo.endswith(demo_root(state.vault_path).name)
    assert f"<code>{where.others}</code>" in html and f"<code>{where.demo}</code>" in html
    assert f"“{SERVICE}”" in html and "~/Library/WebKit/com.4ampersand.taxbinder" in html
    backups = html.split('id="backups"')[1].split("</section>")[0]
    assert backups.count("<tr><td>") == 4  # what each brings back
    assert "A backup alone, no key" in backups and "Nothing." in backups


def test_the_topics_before_there_is_a_binder(tmp_path: Path) -> None:
    from test_start import _state

    fresh = _state(tmp_path / "app" / "vault", tmp_path)  # no binder yet: the Start screen
    c = client(fresh)
    assert 'href="/help#new-mac"' in c.get("/").text
    html = c.get("/help").text
    assert "Moving to a new Mac" in html and "Restore from a backup" in html


def test_a_paired_phone_sees_no_paths(shared: AppState) -> None:  # noqa: F811
    html = pair(shared).get("/help").text
    assert "Uninstalling" in html and "~/Library" not in html and "<code>" not in html


def test_no_page_repeats_an_id(state: AppState) -> None:  # noqa: F811
    """Help's "Seeing a document" section doesn't take the viewer's id, so the page doesn't make
    room for a viewer that isn't open."""
    c = client(state)
    for path in ("/help", "/", "/documents", "/check", "/settings", "/donations", "/payments"):
        ids = re.findall(r'\bid="([^"]+)"', c.get(path).text)
        assert not {i for i in ids if ids.count(i) > 1}, path
