"""Your binder (the deck of years, home), Across the years and Add documents; the other pages'
routes are in `app.py`. These routes are added to the app in `create_app`, so the guard,
sign-in, the entity and the content policy are the same for every page. It only reads here:
every change goes through the app's API (`act()`).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from taxbinder import bench, binder, donations, matrix, preparer
from taxbinder.notices import Notice, notices

if TYPE_CHECKING:
    from taxbinder.store import RecordStore
    from taxbinder.web.app import AppState

HERE = Path(__file__).parent
STATIC = "/static/"

WORD = {"to_come": "to come", "missing": "missing", "gap": "gone?", "in": "here"}

STATIONS = [  # the factory's stations, one per step of `add.Item.steps`, and the end
    ("received", "Intake", "received; exact copies skipped", "M4 7h16M4 7l2 13h12l2-13M9 11h6"),
    ("reading", "Reading", "its text, or a scan read on this Mac",
     "M4 6h16M4 12h10M4 18h13M19 15l3 3-3 3"),
    ("recognised", "Recognised", "what it is, from its printed title",
     "M12 3l2.5 5 5.5.8-4 3.9.9 5.5L12 15.6 7.1 18.2l.9-5.5-4-3.9 5.5-.8z"),
    ("sender", "Sender", "who sent it, matched to your binder", "M3 7h18v12H3zM3 7l9 7 9-7"),
    ("whose", "Whose & year", "whose it is, and its tax year",
     "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0"),
    ("amounts", "Amounts", "its boxes, read by the rules", "M5 4h14v16H5zM9 8h6M9 12h6M9 16h3"),
    ("model", "The AI model", "read again by the AI model, on this Mac",
     "M9 3h6v3H9zM5 6h14v12H5zM9 10h.01M15 10h.01M9 14h6M12 18v3"),
    ("checks", "Checks", "the rule book checks its year",
     "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7zM9 12l2 2 4-4"),
    ("end", "Filed", "into its year, or asks you", "M3 7l3-3h12l3 3v12H3zM3 7h18M9 12h6"),
]  # fmt: skip


def money(amount: Decimal | None) -> str:
    """$1,240, or ($640) for a loss; "" without an amount."""
    if amount is None:
        return ""
    text = f"${abs(amount):,.0f}"
    return f"({text})" if amount < 0 else text


def notice_year(n: Notice) -> int | None:
    """The year a note is about (its card's side panel), or None: about the whole binder."""
    if n.id.startswith("gap:"):
        return int(n.id.rsplit(":", 1)[1])
    return int(n.at) if n.face == "years" and n.at.isdigit() else None


def cards(c: binder.Binder, front: int) -> list[dict[str, Any]]:
    """One card per year, oldest first: its senders down and kinds across (only those with
    something that year), each square's status, its amount and the documents behind it."""
    sides = {s.year: s for s in binder.year_sides(c)}
    out = []
    for y in c.years:
        kinds = [(k, label) for k, label in c.kinds if any(key[1] == k and key[2] == y
                                                          for key in c.squares)]  # fmt: skip
        rows = []
        for i in c.issuers:
            cells = []
            for k, label in kinds:
                sq = c.square(i.id, k, y)
                if sq is None or sq.status == "na":
                    cells.append(None)
                    continue
                cells.append({
                    "status": sq.status, "kind": label, "key": f"{i.id}|{k}",
                    "text": binder.short(sq.amount) if sq.amount is not None else WORD[sq.status],
                    "amount": money(sq.amount), "docs": " ".join(sq.docs),
                    "rows": " ".join(sq.rows),
                    "what": binder.STATUS_TEXT[sq.status] + (
                        f", usually by {c.usual[i.id]}"
                        if sq.status == "to_come" and i.id in c.usual else ""),
                })  # fmt: skip
            if any(cells):
                rows.append({"name": i.name, "ein": i.ein, "cells": cells})
        side = sides[y]
        out.append({"year": y, "front": y == front, "kinds": kinds, "rows": rows, "side": side})
    return out


def row_form(store: RecordStore) -> dict[str, Any]:
    """What "Expect another document" offers (the checklist's Add a row, folded into the deck
    card): document types, built in and the household's own, owners, package folders,
    and the names already known."""
    from typing import get_args

    from taxbinder import doctypes
    from taxbinder.entities import entity_info
    from taxbinder.models.common import BUILTIN_DOC_TYPES
    from taxbinder.models.registry import PackageFolder
    from taxbinder.years import default_owner

    return {
        "labels": doctypes.labels(store), "builtin_types": BUILTIN_DOC_TYPES,
        "custom_types": doctypes.custom_types(store), "folders": get_args(PackageFolder),
        "taxpayers": store.list("taxpayer"), "default_owner": default_owner(store),
        "filer_name": entity_info(store).name,
        "institution_names": sorted({i.name for i in store.list("institution")}),
        "source_names": sorted({s.name for s in store.list("source")}),
    }  # fmt: skip


ASKED = {  # each of Check's questions, in a few words
    "whose": "Whose is this?", "copy": "Two copies of one form?", "type": "What is it?",
    "return": "Which copy of the return?", "scan": "Read from a scan: is it right?",
    "year": "Which tax year?", "issuer": "Who sent it?", "read": "Read two ways",
    "boxes": "Amounts to confirm",
}


def mount(app: FastAPI, state: AppState, page: Callable[..., HTMLResponse],
          need_store: Callable[[Request], HTMLResponse | None],
          pick_year: Callable[[int | None], int],
          year_starts: Callable[[RecordStore], dict[str, Any]],
          year_glance: Callable[[RecordStore, int], dict[str, Any]]) -> None:  # fmt: skip
    templates = Jinja2Templates(directory=str(HERE / "templates"))  # for the panels' parts
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def deck_home(request: Request, year: int | None = None) -> HTMLResponse:
        """The deck of years: the year being worked on in front (or `year`)."""
        from taxbinder import check, journey
        from taxbinder.web.views import checklist_rows

        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            c = binder.build(store, state.today())
            noticed = notices(store, c)
            starts = year_starts(store)  # Start the next year, or an earlier one
            glance = {y: year_glance(store, y) for y in c.years}  # Gather, Check, Hand off
            asked = journey.questions(store, c.years[-1]) if c.years else 0
            waiting = sum(check.SKIPPED not in d.tags for d in check.waiting(store))
            form = row_form(store)
            away = {y: [r for r in checklist_rows(store, y) if r.status[0] == "na"]
                    for y in c.years}  # not expected this year: no square shows them
            papers = {y: preparer.papers(store, y) for y in c.years}  # for my preparer
            for y in c.years:  # last year's papers, as a reminder (never "missing")
                if (due := preparer.reminder(store, y)) is not None:
                    noticed.append(Notice(due[0], due[1], [("Not this year", "dismiss")],
                                          "years", str(y)))  # fmt: skip
        working = pick_year(year if year in c.years else None)  # ?year= sticks, as Results' did
        front = working if working in c.years else (c.years[-1] if c.years else working)
        out = cards(c, front)
        for card in out:  # Noticed for you, beside the year they're about
            card["notices"] = [n for n in noticed if notice_year(n) in (card["year"], None)]
            card["glance"] = glance[card["year"]]
            card["away"] = away[card["year"]]
            card["papers"] = papers[card["year"]]
        return page(request, "home.html", cards=out, front_year=front,
                    asked=asked, waiting=waiting, form=form, **starts)  # fmt: skip

    @app.get("/parts/rows", response_class=HTMLResponse)
    def deck_rows(request: Request, ids: str = "") -> HTMLResponse:
        """A square's checklist rows, with what the checklist did to each: mark it
        checked, not expected this year, its file, amount, comment and portal, or remove it."""
        from taxbinder import matrix, settings
        from taxbinder.web.views import checklist_rows

        if (blocked := need_store(request)) is not None:
            return blocked
        want = ids.split()
        with state.locked_store() as store:
            years = {int(i.split("/")[0]) for i in want if i.split("/")[0].isdigit()}
            rows = {r.e.id: r for y in years for r in checklist_rows(store, y)}
            ticking = settings.ticking_on(store)
            # each row's boxes (the panel names its box, and another can be picked); a row not
            # in Across the years yet is added with the box picked
            mx = {(r.source.id, r.doc_type): r for r in matrix.rows(store, every=True)}
            shown = {(r.source, r.doc_type) for r in matrix.current_rows(store)}
        found = [rows[i] for i in want if i in rows]
        return templates.TemplateResponse(request, "_rows.html", {
            "rows": found, "ticking": ticking, "mx": mx, "shown": shown})

    @app.get("/parts/asks", response_class=HTMLResponse)
    def deck_asks(request: Request) -> HTMLResponse:
        """Questions for you, on Add documents: Check's questions about the
        documents waiting, answered where they were added, then "Looks good, file them"."""
        from taxbinder import check, doctypes

        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            found = check.report(store)
            people = {t.id: t for t in store.list("taxpayer")}
            institutions = sorted(store.list("institution"), key=lambda i: i.name.lower())
            labels = doctypes.labels(store)
        ready = sum(len(g.items) for g in found.groups) + sum(
            1 for f in found.later if check.NOT_TAX in f.doc.tags)  # what filing files
        return templates.TemplateResponse(request, "_asks.html", {
            "found": found, "ready": ready, "people": people, "institutions": institutions,
            "labels": labels, "year": pick_year(None), "questions": ASKED})  # fmt: skip

    @app.get("/years", response_class=HTMLResponse)
    def deck_years(request: Request) -> HTMLResponse:
        """Across the years (Across the years' table): every row names its sender, and
        the account when the sender has several; grouped by sender."""
        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            cols, rows = matrix.years(store), matrix.rows(store, state.today())
            picks = matrix.choices(store)
            short = len(matrix.short_of_key_boxes(store))  # W-2 rows from before tax withheld
            given, given_total = donations.by_year(store)
        rows.sort(key=lambda r: (r.sender.casefold(), r.label, r.account.casefold()))
        shown = {r.key for r in rows}
        choices = [c for c in picks if f"{c[0]}|{c[1]}" not in shown]  # rows that can be added
        cols = sorted(cols, reverse=True)  # newest on the left
        return page(request, "years.html", cols=cols, rows=rows,
                    choices=choices, short=short, given=given, given_total=given_total,
                    whole=matrix.whole)  # fmt: skip

    @app.get("/add", response_class=HTMLResponse)
    def deck_add(request: Request, year: int | None = None) -> HTMLResponse:
        """Adding documents as a factory: each document through every step, the
        model's included, shown as it happens (`add.Item.steps`, from /api/add/status)."""
        if (blocked := need_store(request)) is not None:
            return blocked
        from taxbinder import results

        batch = request.app.state.reader.batch.summary()
        year = pick_year(year)
        with state.locked_store() as store:
            mail = results.mail(store, year)  # what doesn't come in the mail
        return page(request, "add.html", stations=STATIONS, batch=batch,
                    mail=mail, mail_year=year)  # fmt: skip

    @app.get("/bench/{doc_id}")
    def deck_bench(doc_id: str) -> JSONResponse:
        """The reading bench for one document: both readings, what's kept, its checks."""
        from taxbinder.store import RecordNotFound

        with state.locked_store() as store:
            if store is None:
                return JSONResponse({"error": state.current_problem() or "no vault"}, 503)
            try:
                return JSONResponse(bench.bench(store, doc_id))
            except (RecordNotFound, ValueError):
                return JSONResponse({"error": "no such document"}, 404)
