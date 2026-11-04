"""Accessibility: the top bar, Check, the recovery-key step and backup /
restore work with the keyboard alone and have VoiceOver labels; larger text is the app shell's
zoom (its steps are a Rust test). Checked here through each page's markup."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from pdfgen import make_pdf
from test_start import _state
from test_web import client, state  # noqa: F401 - `state` is a fixture

from taxbinder.ingest.inbox import ingest_file
from taxbinder.web.app import AppState

STATIC = Path(__file__).parents[1] / "src" / "taxbinder" / "web" / "static"


class Page(HTMLParser):
    """Inputs without a label, buttons without a name, dialogs without a title."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.labelled: set[str] = set()  # <label for=…>
        self.fields: list[dict] = []
        self.buttons: list[list] = []  # [attrs, text]
        self.dialogs: list[dict] = []
        self.label_depth = 0
        self.button: list | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: v or "" for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.labelled.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.fields.append({**a, "_in_label": self.label_depth > 0})
        if tag == "button":
            self.button = [a, ""]
        if a.get("role") == "dialog":
            self.dialogs.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self.label_depth -= 1
        if tag == "button" and self.button is not None:
            self.buttons.append(self.button)
            self.button = None

    def handle_data(self, data: str) -> None:
        if self.button is not None:
            self.button[1] += data

    def problems(self) -> list[str]:
        out = []
        for f in self.fields:
            if not (f["_in_label"] or f.get("id") in self.labelled or f.get("aria-label")
                    or f.get("aria-labelledby")):  # fmt: skip
                out.append(f"field without a label: {f.get('id') or f.get('class')}")
        for attrs, text in self.buttons:
            if not (text.strip() or attrs.get("aria-label")):
                out.append(f"button without a name: {attrs.get('id') or attrs.get('class')}")
        for d in self.dialogs:
            if d.get("aria-modal") != "true" or d.get("aria-labelledby") not in self.ids:
                out.append(f"dialog without aria-modal or a title: {d.get('aria-labelledby')}")
        return out


def _check(html: str) -> Page:
    p = Page()
    p.feed(html)
    assert p.problems() == []
    return p


def test_the_top_bar_and_every_page_start(state: AppState) -> None:  # noqa: F811
    html = client(state).get("/").text
    page = _check(html)
    assert html.index('class="skip-link" href="#page"') < html.index('class="topnav-links"')
    assert 'id="page"' in html and "page" in page.ids
    bar = html.split('class="topnav-links"')[1].split("</nav>")[0]
    assert 'aria-current="page">Your binder' in bar  # VoiceOver hears where you are
    assert "&#34;" not in bar  # never escaped


def test_check(state: AppState) -> None:  # noqa: F811
    inbox = state.store.vault.inbox / "letter.pdf"  # arrives in the inbox: waits for Check
    inbox.write_bytes(make_pdf(["A letter about nothing in particular", "2025"]))
    ingest_file(state.store, inbox, "inbox", "test", waiting=True)
    html = client(state).get("/check").text
    _check(html)
    assert 'role="group" aria-label="What is it?' in html  # the question names its document
    assert 'aria-label="Change what was read: ' in html and 'aria-label="Skip for now: ' in html
    assert 'id="chk-why"' in html and 'aria-describedby="chk-why"' in html  # the reason, shown


def test_the_recovery_key_step_and_backup(tmp_path: Path) -> None:
    from test_safe import _started

    _, c = _started(tmp_path)  # a binder made by Get started: its key waits to be shown
    html = c.get("/backup").text
    _check(html)
    assert 'id="safe-key" tabindex="-1" role="textbox" aria-readonly="true"' in html
    assert 'aria-label="Your recovery key"' in html and 'id="backup-now"' in html


def test_restore_and_unlock(tmp_path: Path) -> None:
    s = _state(tmp_path / "app" / "vault", tmp_path)  # a Mac with no binder yet
    c = client(s)
    for path in ("/start/restore", "/start/unlock", "/"):
        html = c.get(path).text
        _check(html)
        assert 'role="status"' in html  # progress and errors are announced


def test_dialogs_keep_the_keyboard_and_give_focus_back() -> None:
    js = (STATIC / "pages.js").read_text()
    assert 'if (e.key !== "Tab") return;' in js and "opener.focus()" in js
    assert '$("safe-key").focus()' in js


def test_larger_text_keeps_the_menu_whole() -> None:
    """At 200% (or a narrow window) the top bar wraps and scrolls away: its Settings, Help and
    Last backup links and the badges must stay."""
    css = (STATIC / "app.css").read_text()  # the top bar, on every page
    narrow = css.split("@media (max-width: 860px) {")[1].split("}\n")[0] + "}"
    assert ".topnav-foot { display: none }" not in narrow and ".topnav { position: static" in narrow
    assert ".topnav-links small { display: none }" not in css
