"""Home (the deck of years), Across the years, Add documents, and the top bar every page shares."""

from __future__ import annotations

import re

from pdfgen import make_pdf
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder.web.app import AppState


def test_the_deck_of_years(state: AppState) -> None:  # noqa: F811
    html = client(state).get("/").text
    years = re.findall(r'class="tab" id="t-(\d{4})"', html)
    assert years and years == sorted(years)  # a card and a tab for every year
    assert len(re.findall(r'class="card( front)?"', html)) == len(years)
    assert html.count('class="card front"') == 1 and 'data-front="2025"' in html
    for y in years:
        assert f'aria-label="Bring {y} to the front"' in html
    # every square is a button that says what it is
    squares = re.findall(r'<button type="button" class="sq s-(\w+)"[^>]*aria-label="([^"]+)"', html)
    assert squares and all(label.startswith("2025, ") for _, label in squares)
    assert {s for s, _ in squares} <= {"in", "to_come", "missing", "gap"}
    # only the card in front can be tabbed into
    assert html.count(" inert aria-hidden") == len(years) - 1


def test_deck_keeps_the_page_policy(state: AppState) -> None:  # noqa: F811
    c = client(state)
    html = c.get("/").text
    assert ' style="' not in html and "<style" not in html  # styles only from app.css
    assert re.findall(r"<script[^>]*>", html) == [
        '<script src="/static/bar.js">', '<script src="/static/app.js">']
    r = c.get("/static/app.css")
    assert r.status_code == 200 and "no-store" not in r.headers.get("cache-control", "")
    assert "script-src 'self'" in c.get("/").headers["content-security-policy"]


def test_every_page_has_the_same_top_bar(state: AppState) -> None:  # noqa: F811
    """Home is the deck of years; every page has the same top bar."""
    assert client(state, login=False).get("/").status_code == 401
    c = client(state)
    home = c.get("/").text
    assert 'class="deck"' in home
    bars = {path: c.get(path).text.split('<header class="topnav">')[1].split("</header>")[0]
            for path in ("/", "/years", "/add", "/documents", "/settings", "/check")}
    for path, bar in bars.items():
        assert 'href="/years"' in bar and 'href="/add"' not in bar, path  # not in the bar
        assert re.sub(r' on\b| class="(on)?"| aria-current="page"', "", bar) == re.sub(
            r' on\b| class="(on)?"| aria-current="page"', "", bars["/"]), path  # the same bar


def test_across_the_years_names_the_sender(state: AppState) -> None:  # noqa: F811
    """Across the years: every row names its sender (the institution), and the account
    under it when it isn't just the sender's name again; the rows to add are named the same way."""
    from taxbinder import matrix

    c = client(state)
    c.post("/api/matrix/add-all", headers=ORIGIN)
    with state.locked_store() as store:
        rows = matrix.rows(store)
        labels = [label for _, _, label in matrix.choices(store)]
    assert rows and all(r.sender for r in rows)
    brackley = [r for r in rows if r.sender == "Brackley Wealth"]
    assert brackley and all(r.account for r in brackley)  # its accounts, told apart
    assert any(label.startswith("Brackley Wealth, ") for label in labels)
    html = c.get("/years").text
    for r in rows:
        assert f'data-sender="{r.sender}"' in html
    assert f"<small>{brackley[0].account}" in html
    assert ' style="' not in html


def test_across_the_years_can_be_edited(state: AppState) -> None:  # noqa: F811
    """Amounts typed, a row's box chosen, rows added and removed, all on the page, through the
    app's API (undoable)."""
    from taxbinder import matrix

    c = client(state)
    c.post("/api/matrix/add-all", headers=ORIGIN)
    with state.locked_store() as store:
        rows = matrix.rows(store)
    html = c.get("/years").text
    assert 'href="/matrix"' not in html and 'href="/matrix.xlsx"' in html
    assert html.count('class="link rm-row"') == len(rows)
    lines = sum(1 + len(r.subs) for r in rows)  # a row's other boxes: sub-rows beneath it
    assert html.count('class="num ed') == lines * len(matrix.years(state.store))
    assert 'class="bx"' in html and '<option value="__other">' in html
    assert 'Every document the binder expects already has a row.' in html
    assert ' style="' not in html
    # a row removed can be added again from the page
    r = rows[0]
    body = {"source": r.source.id, "doc_type": r.doc_type}
    assert c.post("/api/matrix/remove", json=body, headers=ORIGIN).status_code == 200
    html = c.get("/years").text
    assert f'<option value="{r.source.id}|{r.doc_type}">' in html


def test_noticed_for_you_beside_its_year(state: AppState) -> None:  # noqa: F811
    """The binder's notes sit beside the year they're about; one click answers one."""
    from taxbinder.models import Institution
    from taxbinder.notices import Notice
    from taxbinder.web.binder_views import notice_year

    assert notice_year(Notice("gap:lrb:1099_int:2025", "", [], "docs", "1099_int")) == 2025
    assert notice_year(Notice("corrected:x:2024", "", [], "years", "2024")) == 2024
    assert notice_year(Notice("ein:a:b", "", [])) is None  # the whole binder: every year
    with state.locked_store() as store, store.operation("test", "two names, one EIN"):
        store.put(Institution(id="twin-a", name="Twin Fund A", tin="00-1234567"), None)
        store.put(Institution(id="twin-b", name="Twin Fund B", tin="00-1234567"), None)
    html = client(state).get("/").text
    assert 'data-notice="ein:' in html and "Noticed for you" in html
    assert 'data-action="keep:twin-a"' in html and 'data-action="keep:twin-b"' in html
    assert "Twin Fund B" in html and "No documents" in html  # each name, with its documents


def test_adding_documents_as_a_factory(state: AppState) -> None:  # noqa: F811
    """Add documents: a station for every step the reader reports, the files sent to the
    reader's queue, the same page policy; home leads here."""
    from taxbinder import add
    from taxbinder.web.binder_views import STATIONS

    c = client(state)
    html = c.get("/add").text
    steps = re.findall(r'<li class="st" data-step="(\w+)"', html)
    assert steps == [*add.STEPS, "end"] == [s[0] for s in STATIONS]
    assert 'id="fx-drop"' in html and 'id="fx-files"' in html and 'id="fx-cards"' in html
    assert ' style="' not in html
    assert re.findall(r"<script[^>]*>", html) == [
        '<script src="/static/bar.js">', '<script src="/static/app.js">',
        '<script src="/static/add.js">']
    assert c.get("/static/add.js").status_code == 200
    assert 'href="/add?year=' in c.get("/").text  # Your binder leads here


def test_the_reading_bench_page(state: AppState) -> None:  # noqa: F811
    """The bench beside the belt, and its data for one document (the reader reports the
    document's id); an unknown one is a 404."""
    from test_extract_forms import _w2

    from taxbinder.ingest.inbox import ingest_file

    c = client(state)
    html = c.get("/add").text
    assert 'id="bench"' in html and all(f'id="bn-{k}"' in html for k in ("rules", "model", "kept", "checks"))
    with state.locked_store() as store:
        path = store.vault.inbox / "w2.pdf"
        path.write_bytes(_w2())
        doc = ingest_file(store, path, "upload", "t").doc_id
    b = c.get(f"/bench/{doc}").json()
    assert b["doc"] == doc and {r["box"] for r in b["kept"]} >= {"1", "2"}
    keys = [r["key"] for r in b["kept"]]  # a W-2's key boxes first, the rest after
    assert {r["box"] for r in b["kept"] if r["key"]} >= {"1", "2"} and keys == sorted(keys, reverse=True)
    assert not [r for r in b["kept"] if r["key"] and r["box"] not in ("1", "2", "16", "17")]
    assert c.get("/bench/doc_000000000000").status_code == 404
    assert client(state, login=False).get(f"/bench/{doc}").status_code == 401


def test_what_doesnt_come_in_the_mail_after_adding(state: AppState) -> None:  # noqa: F811
    """On the factory page, each common thing no form reports, with Add, None this year
    and Not sure; an answer (through `act()`) shows when the page comes back."""
    c = client(state)
    html = c.get("/add").text
    assert html.count('class="mrow"') == 5 and 'What doesn’t come in the mail' in html
    assert 'data-answer="no"' in html and 'href="/donations?add=1"' in html
    year = re.search(r'id="mail" data-year="(\d{4})"', html).group(1)
    r = c.post("/api/results/answer", json={"year": int(year), "kind": "vehicle", "value": "no"},
               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200 and r.json()["undo"]["sha"]
    row = re.search(r'data-kind="vehicle">(.*?)</div>\s*</div>', c.get("/add").text, re.S).group(1)
    assert re.search(r'<span class="said">(None this year|That’s all)</span>', row)  # not hidden


def test_questions_answered_on_add_documents(state: AppState) -> None:  # noqa: F811
    """Once the reading ends, Add documents asks Check's questions about what's
    waiting, answered there, then "Looks good, file them"; no trip to Check."""
    from test_check import drop

    c = client(state)
    html = c.get("/add").text
    assert 'id="fx-asks"' in html
    assert c.get("/parts/asks").text.count('class="ask"') == 0  # nothing waiting
    with state.locked_store() as store:
        doc = drop(store, "k1.pdf", make_pdf(["Schedule K-1 (Form 1065) 2025", "Partner number 7"]))
    asks = c.get("/parts/asks").text
    assert f'id="ask-{doc}"' in asks and 'data-left="1"' in asks and "question for you" in asks
    assert 'data-a="mark" data-v="skip"' in asks and ' style="' not in asks
    assert c.post("/api/check/mark", json={"id": doc, "what": "not_tax"}, headers=ORIGIN).status_code == 200
    asks = c.get("/parts/asks").text
    assert 'data-left="0"' in asks and 'data-a="file"' in asks and 'data-ready="1"' in asks
    assert c.post("/api/check/confirm", headers=ORIGIN).json()["filed"] == 1
    assert 'data-ready="0"' in c.get("/parts/asks").text
    assert client(state, login=False).get("/parts/asks").status_code == 401
