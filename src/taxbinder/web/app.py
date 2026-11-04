"""FastAPI app served to the desktop shell's main window (§11.4, §12.2).

Security model (the app window's):
* 127.0.0.1 on an OS-chosen port.
* Host must be exactly 127.0.0.1:<port> (DNS-rebinding guard).
* State-changing requests must carry a matching Origin.
* Everything except /bootstrap, /healthz and /static needs the session cookie, obtained once
  via /bootstrap?t=<one-time code>; the code only travels over the shell↔backend pipe.
  The cookie is HttpOnly + SameSite=Strict.
* No inline scripts (CSP script-src 'self').

Sharing on the home network (`sharing.py`, off until turned on in the app window): a second,
HTTPS listener. There, Host and Origin must be this Mac's `<name>.local:<port>` or LAN IP, and
every page except pairing needs a paired device's cookie. Paired devices can't use the actions
that drive this Mac (portal windows, Save Page) or manage sharing (`LOCAL_ONLY`).
"""

from __future__ import annotations

import asyncio
import contextvars
import getpass
import logging
import re
import secrets
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from taxbinder import (
    add,
    backup,
    bills,
    diagnostics,
    doctypes,
    downloads,
    foreign,
    journey,
    matrix,
    ocr,
    perf,
    preparer,
    privacy,
    results,
    returns,
    review,
    safe,
    settings,
    states,
    viewer,
    whose,
)
from taxbinder import donations as gifts
from taxbinder import help as helptext
from taxbinder import household as home
from taxbinder.claims import (
    PAYMENT_CATEGORIES,
    PAYMENT_KINDS,
    add_payment,
    delete_claim,
    edit_payment,
    payment_row,
    payment_rows,
)
from taxbinder.entities import HOUSEHOLD, entity_info, filer_for, list_entities
from taxbinder.entities import describe as describe_entity
from taxbinder.entities import vault_path as entity_vault
from taxbinder.ingest.inbox import ingest_file, needs_ocr, rematch
from taxbinder.ingest.pdftext import pdf_info, sniff_mime
from taxbinder.models.common import BUILTIN_DOC_TYPES
from taxbinder.models.registry import RETURN_FORMS
from taxbinder.models.year import YearConfig
from taxbinder.report import DOC_LABELS, year_status
from taxbinder.review import link_document, set_applicability, set_verified
from taxbinder.rules.params import checked_years, load_params
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordNotFound, RecordStore, RevConflict
from taxbinder.store.records import live_changes
from taxbinder.web import binder_views, views
from taxbinder.web import sharing as lan
from taxbinder.web.control import Emitter, emit
from taxbinder.years import (
    CarryOver,
    add_row,
    carry_over,
    delete_row,
    edit_row,
    new_year,
    parse_amount,
    years_in_vault,
)

COOKIE = "tp_session"
PUBLIC_PATHS = {"/bootstrap", "/healthz", "/favicon.ico"}
STATIC = (binder_views.STATIC,)  # the pages' styles and scripts: no sign-in, cached
LAN_PUBLIC_PATHS = {"/pair", "/api/pair", "/healthz", "/favicon.ico"}
LOCAL_ONLY = {  # never from a paired device: they drive this Mac or manage sharing
    "/bootstrap",
    "/sharing",
    "/api/sharing",
    "/api/pairing",
    "/api/devices/revoke",
    "/api/open-portal",
    "/api/capture-page",
    "/api/print",  # the print panel opens on this Mac
    "/api/documents/save",  # writes into this Mac's Downloads folder
    "/api/entities",  # creates a vault (and a Keychain entry) on this Mac
    "/api/entity/rename",
    "/api/bills/calendar",  # opens Calendar on this Mac
    # redaction (Stage 1): only at this Mac, where the person can see what leaves it
    "/privacy",
    "/api/privacy",
    "/api/privacy/remove",
    "/api/redact",
    "/api/redact/save",
    "/api/unredact",
    "/api/matrix/save",  # writes into this Mac's Downloads folder
    "/api/preparer/save",  # writes into this Mac's Downloads folder
    "/backup",
    "/api/backup",  # writes into this Mac's Downloads folder
    "/api/safe/key",  # the recovery key (or this Mac's key): only ever shown at this Mac
    "/api/safe/confirm",
    "/api/safe/later",
    "/api/demo",  # opens, resets or leaves the demo binder on this Mac
    "/api/demo/add",
    "/settings",
    "/api/settings",  # how this Mac opens websites; other filers
    "/api/settings/reveal-inbox",  # opens Finder on this Mac
    "/api/years/set",
    "/start/restore",  # Start: the vault and its key are this Mac's
    "/start/unlock",
    "/api/start",
    "/api/start/unlock",
    "/api/start/restore",
    "/api/start/retry",
    "/api/diagnostics",  # this Mac's versions and recent errors, to copy into an email here
    "/api/reading",  # the reading model on this Mac: what's here, downloads
    "/api/reading/download",
    "/api/reading/cancel",
    "/api/reading/use",
    "/api/reading/delete",
    "/api/reading/check",
}
FILE_CSP = "sandbox; default-src 'none'"  # a downloaded file never runs as part of the app
MAX_URL_LEN = 2048
MAX_UPLOAD = 50 * 1024 * 1024
UPLOAD_TYPES = add.KINDS  # what can be added, by content
UPLOAD_EXTS = ["pdf", "png", "jpg", "jpeg", "heic", "html", "htm"]  # the file pickers' choice
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
log = logging.getLogger("taxbinder.web")
HERE = resources.files(__package__)


# The entity the current request works on (set by the guard per request; requests from the
# app window and from each paired device keep their own choice). Unset → the app window's.
CURRENT_ENTITY: contextvars.ContextVar[str | None] = contextvars.ContextVar("entity", default=None)


@dataclass
class AppState:
    vault_path: Path  # the household vault; other entities live beside it (entities.py)
    store: RecordStore | None = None  # the household's store
    problem: str | None = None  # why there is no store: no vault, no key, vault in use
    problem_kind: str | None = None  # "new" (no vault yet), "nokey", "other" (start.py)
    emit: Emitter = emit
    port: int = 0
    today: Callable[[], date] = date.today
    _own_today: Callable[[], date] | None = None  # while the demo's fixed date is in use
    year: int | None = None  # the year picked in the header; sticks while the app runs
    lock: threading.Lock = field(default_factory=perf.TimedLock)  # type: ignore[assignment]
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    bootstrap_code: str | None = field(default_factory=lambda: secrets.token_urlsafe(24))
    extra_hosts: set[str] = field(default_factory=set)  # tests only
    local_actor: str = field(default_factory=lambda: f"app:{getpass.getuser()}")
    downloads_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    open_file: Callable[[Path], None] = field(  # macOS `open`: an .ics → Calendar (tests swap it)
        default=lambda path: subprocess.run(["/usr/bin/open", str(path)], check=False)  # noqa: S603
    )
    open_url: Callable[[str], None] = field(  # a website in the Mac's browser (tests swap it)
        default=lambda url: subprocess.run(["/usr/bin/open", url], check=False)  # noqa: S603
    )
    in_app: bool = False  # inside the app window (the shell reads our control messages)
    sharing: lan.Sharing | None = None  # set while shared on the network
    pairing: lan.Pairing | None = None  # open pairing code, if any
    _devices: lan.Devices | None = None
    selected: dict[str, str] = field(default_factory=dict)  # client ("local", device id) → entity
    _entity_stores: dict[str, RecordStore] = field(default_factory=dict)
    _entity_problems: dict[str, str] = field(default_factory=dict)
    _entity_kinds: dict[str, str] = field(default_factory=dict)  # "nokey" or "other"
    home_vault: Path | None = None  # the person's own vault while the demo is open
    model_download: Any = None  # llm.Download while a model's files come in
    model_reader: Any = None  # modelread.ModelReader, started by the sidecar

    @property
    def demo(self) -> bool:
        return self.home_vault is not None

    @property
    def models_dir(self) -> Path:
        """The reading models' folder: one per computer, the demo's included (llm.folder)."""
        from taxbinder import llm

        return llm.folder(self.home_vault or self.vault_path)

    def open(self) -> None:
        """Open the household's vault (if it isn't open), or note why not (the Start page offers
        what to do); other filers' binders are opened again on next use."""
        from taxbinder import start

        if self.store is None:
            self.store, self.problem, self.problem_kind = start.open_store(self.vault_path)
        self._entity_problems.clear()  # an entity's missing key may be back too
        self._entity_kinds.clear()
        if self.problem:
            log.warning("vault not opened: %s", self.problem)

    # -- entities -----------------------------------------------------------------------

    def current_entity(self) -> str:
        return CURRENT_ENTITY.get() or self.selected.get("local", HOUSEHOLD)

    def store_for(self, slug: str) -> RecordStore | None:
        """The entity's store, opened on first use (and kept open, like the household's)."""
        if slug == HOUSEHOLD:
            return self.store
        if slug not in self._entity_stores and slug not in self._entity_problems:
            from taxbinder import start

            try:  # the same words and actions as the household's binder
                store, problem, kind = start.open_store(entity_vault(self.vault_path, slug))
            except Exception as e:  # noqa: BLE001 - shown on the setup page instead
                store, problem, kind = None, f"This binder didn't open: {e}", "other"
            if kind == "new":  # its folder is gone: never the household's Get started
                problem, kind = "This filer's binder isn't on this Mac any more.", "other"
            if store is not None:
                self._entity_stores[slug] = store
            else:
                self._entity_problems[slug] = problem or ""
                self._entity_kinds[slug] = kind or "other"
        return self._entity_stores.get(slug)

    def current_problem_kind(self) -> str | None:
        slug = self.current_entity()
        return self.problem_kind if slug == HOUSEHOLD else self._entity_kinds.get(slug)

    def current_store(self) -> RecordStore | None:
        return self.store_for(self.current_entity())

    def current_problem(self) -> str | None:
        slug = self.current_entity()
        return self.problem if slug == HOUSEHOLD else self._entity_problems.get(slug)

    def entity_choices(self) -> list[tuple[str, Any]]:
        """(slug, EntityInfo) for every entity whose vault opens."""
        out = []
        for ref in list_entities(self.vault_path):
            s = self.store_for(ref.slug)
            if s is not None or ref.slug == HOUSEHOLD:
                out.append((ref.slug, entity_info(s)))
        return out

    def locked_entities(self) -> list[tuple[str, str]]:
        """(slug, name) for every other filer whose key is missing on this Mac: listed in the
        switcher, where choosing one asks for the recovery key."""
        out = []
        for ref in list_entities(self.vault_path)[1:]:
            if self.store_for(ref.slug) is None and self._entity_kinds.get(ref.slug) == "nokey":
                out.append((ref.slug, ref.slug.replace("-", " ").title()))
        return out

    def close_all(self) -> None:
        for s in self._entity_stores.values():
            s.close()
        self._entity_stores.clear()

    # -- the demo (demo.py): a binder of its own, its own key file, never the Keychain --

    def _switch(self, path: Path, home_vault: Path | None) -> None:
        """Close every store and open the vault at `path` (callers hold the lock)."""
        from taxbinder import demo
        from taxbinder.store import keys

        self.close_all()
        if self.store is not None:
            self.store.close()
        self.store, self.selected, self.year = None, {}, None
        self.home_vault, self.vault_path = home_vault, path
        keys.use(demo.keys(home_vault) if home_vault else None)
        if home_vault is not None:  # in the demo, its own fixed today; yours again on leaving
            self._own_today = self._own_today or self.today
            self.today = lambda: demo.TODAY
        elif self._own_today is not None:
            self.today, self._own_today = self._own_today, None
        self.open()

    def enter_demo(self, fresh: bool = False) -> None:
        """Open the demo (set up the first time, or again with `fresh`: Start the demo over)."""
        from taxbinder import demo

        home_vault = self.home_vault or self.vault_path
        if self.demo:
            self._switch(home_vault, None)  # closed before its folder can go
        if fresh or not demo.ready(home_vault):
            demo.create(home_vault)  # from scratch (a half-made one from before goes too)
        self._switch(demo.vault_path(home_vault), home_vault)

    def leave_demo(self) -> None:
        if self.home_vault is not None:
            self._switch(self.home_vault, None)

    @property
    def devices(self) -> lan.Devices:
        if self._devices is None:
            self._devices = lan.Devices.load(self.vault_path / "private" / "devices.json")
        return self._devices

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}"} | self.extra_hosts

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self.allowed_hosts}

    @contextmanager
    def locked_store(self) -> Iterator[RecordStore | None]:
        """All store use goes through here (the current entity's store, under the one lock the
        watcher thread and requests share)."""
        with self.lock:
            yield self.current_store()


def validate_portal_url(url: str) -> str | None:
    if not url or len(url) > MAX_URL_LEN:
        return "URL missing or too long"
    parts = urlsplit(url)
    if parts.username or parts.password:
        return "URLs with embedded credentials are not allowed"
    if parts.scheme != "https" or not parts.hostname:
        return "Only https:// portal URLs are allowed"
    return None


def safe_upload_name(raw: str | None, ext: str) -> str:
    """A plain file name for the inbox: no directories or odd characters; extension from content."""
    name = Path(unquote(raw or "").replace("\\", "/")).name
    stem = re.sub(r"[^\w .()&,+-]", "_", Path(name).stem).strip(" .")[:120]
    return f"{stem or 'upload'}.{ext}"


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="TaxBinder", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.tp = state
    diagnostics.watch()  # recent warnings and errors, in memory, for Copy diagnostic info
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    def unauthenticated(request: Request, path: str) -> Response:
        log.info("refused, not signed in: %s", path)  # the path only, never its query string
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return templates.TemplateResponse(request, "locked.html", status_code=401)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        path = request.url.path
        host = request.headers.get("host", "").lower()
        is_write = request.method not in ("GET", "HEAD", "OPTIONS")
        shared = state.sharing
        request.state.remote, request.state.device = False, None
        request.state.actor = state.local_actor

        if request.scope.get(lan.LAN_SCOPE_KEY):  # the network listener (sharing.lan_app)
            if shared is None:  # turned off while a request was in flight
                return PlainTextResponse("Sharing is off", status_code=503)
            if host not in shared.hosts:
                return PlainTextResponse("Misdirected request", status_code=421)
            if is_write and request.headers.get("origin") not in shared.origins:
                return PlainTextResponse("Bad origin", status_code=403)
            if path in LOCAL_ONLY:
                return JSONResponse({"error": "only on the Mac running TaxBinder"}, status_code=403)
            if state.demo:  # the demo is never shared on the network
                return PlainTextResponse("TaxBinder is showing its demo just now", status_code=503)
            request.state.remote = True
            if path not in LAN_PUBLIC_PATHS and not path.startswith(STATIC):
                device = state.devices.check(request.cookies.get(lan.DEVICE_COOKIE))
                if device is None:
                    if path.startswith("/api/"):
                        return JSONResponse({"error": "unauthenticated"}, status_code=401)
                    return RedirectResponse("/pair", status_code=303)
                request.state.device = device
                request.state.actor = f"app:{lan.device_slug(device.name)}"
        else:  # the app window, on loopback
            if host not in state.allowed_hosts:
                return PlainTextResponse("Misdirected request", status_code=421)
            if is_write and request.headers.get("origin") not in state.allowed_origins:
                return PlainTextResponse("Bad origin", status_code=403)
            if path not in PUBLIC_PATHS and not path.startswith(STATIC):
                cookie = request.cookies.get(COOKIE, "")
                if not (cookie and secrets.compare_digest(cookie, state.session_secret)):
                    return unauthenticated(request, path)
        device = request.state.device
        request.state.client = f"device:{device.id}" if device else "local"
        entity = state.selected.get(request.state.client, HOUSEHOLD)
        token = CURRENT_ENTITY.set(entity)  # this request works on that entity's vault
        try:
            resp = await call_next(request)
        finally:
            CURRENT_ENTITY.reset(token)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if "content-security-policy" not in resp.headers:  # file downloads set their own
            resp.headers["Content-Security-Policy"] = CSP
        if request.state.remote:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if not path.startswith(STATIC):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    from starlette.routing import Match

    @app.middleware("http")  # added last: outermost, so it times the whole request
    async def timed(request: Request, call_next: Callable[[Request], Any]) -> Response:
        """The performance log (perf.py): each request's route and time, never its address."""
        path = request.url.path
        if path.startswith(STATIC):
            return await call_next(request)
        route = next((r.path for r in app.router.routes  # type: ignore[attr-defined]
                      if r.matches(request.scope)[0] == Match.FULL), "?")  # fmt: skip
        t = time.perf_counter()
        status = 500
        with perf.request(route) as (n, tallies):
            try:
                resp = await call_next(request)
                status = resp.status_code
                return resp
            finally:
                perf.write("request", req=n, method=request.method, route=route, status=status,
                           ms=round((time.perf_counter() - t) * 1000, 1),
                           tallies=perf.tallies(tallies))  # fmt: skip

    def page(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
        """A page, with what every page's top bar needs."""
        current = tax_year(None)
        with state.locked_store() as store, perf.step("page.bar"):  # what every top bar needs
            years = years_in_vault(store) if store else []
            open_count = views.open_count(store, current) if store else 0
            due = bills.due_soon(store, state.today()) if store else []
            gift_summary = gifts.summary(gifts.donation_rows(store, current) if store else [])
            entity = entity_info(store)
            entities = state.entity_choices()
            locked = state.locked_entities() if not request.state.remote else []
            other_filers = settings.other_filers_on(state.store, state.vault_path)
            remote = request.state.remote
            steps = (
                journey.steps(store, current, state.vault_path, state.today(),
                              remote or state.demo)  # no Keep it safe in the demo
                if store
                else journey.preview()
            )
            binder = len(store.list("document")) if store else 0
            batch = reader.batch
            for s in steps if batch.running else []:  # while reading: "reading 12 of 48"
                if s.id == "add":
                    s.badge, s.news = f"reading {batch.done + 1} of {len(batch.items)}", False
        with perf.step(f"page.render:{name}"):
            return templates.TemplateResponse(
                request,
                name,
                {
                    "vault": state.vault_path,
                    "ready": store is not None,
                    "active": name,
                    "page_help": helptext.for_page(name),
                    "years": sorted({*years, current}) if store else [],
                    "current_year": current,
                    "open_count": open_count,
                    "due_count": len(due),
                    "letters_missing": (
                        gift_summary["letters_missing"] + gift_summary["forms_missing"]
                    ),
                    "overdue": any(c.plan.due < state.today() for c in due),
                    "entity": entity,
                    "entity_slug": state.current_entity(),
                    "entity_desc": describe_entity(entity),
                    "entities": entities,
                    "locked_entities": locked,
                    "other_filers": other_filers,
                    "return_forms": RETURN_FORMS,
                    "remote": request.state.remote,
                    "device": request.state.device,
                    "in_app": state.in_app and not request.state.remote,
                    "steps": steps,
                    "here": journey.PAGES.get(name),
                    "in_binder": name in journey.BINDER,
                    "binder_count": binder,
                    "last_backup": journey.last_backup(state.vault_path, state.today()),
                    "upload_exts": UPLOAD_EXTS,
                    "sharing": state.sharing,
                    "demo": state.demo,
                    **context,
                },
                status_code=status_code,
            )

    def need_store(request: Request) -> HTMLResponse | None:
        if state.current_store() is None:
            return start_page(request)
        return None

    def start_page(request: Request, mode: str = "") -> HTMLResponse:
        """Start: Get started, or put things right, in place of every page while there's
        no vault to show. `mode`: the restore or unlock form."""
        from taxbinder import llm

        kind = state.current_problem_kind()
        return page(request, "start.html", problem=state.current_problem(), kind=kind,
                    mode=mode, status_code=503, memory=round(llm.memory_gb()),
                    small=not llm.enough_memory())  # fmt: skip

    # -- session --------------------------------------------------------------------------

    def keep_it_safe_next() -> bool:
        """After filing on Check: Keep it safe, unless done or put off ("Remind me later")."""
        p = state.vault_path
        return not state.demo and not safe.done(p) and not safe.reminded(p, state.today())

    def next_path(store: RecordStore | None) -> str:
        """Where the app opens: the next thing to do (callers hold the lock)."""
        if store is None:
            return "/"
        y = tax_year(None)
        later = state.demo or safe.reminded(state.vault_path, state.today())
        steps = journey.steps(store, y, state.vault_path, state.today())
        return journey.next_step(steps, later).href

    @app.get("/bootstrap")
    def bootstrap(request: Request, t: str = "") -> Response:
        code = state.bootstrap_code
        if not code or not t or not secrets.compare_digest(t, code):
            return templates.TemplateResponse(request, "locked.html", status_code=403)
        state.bootstrap_code = None  # one-time
        log.info("session established")
        # Not a redirect: the window arrives here from the app's own loading page (another
        # site), and WebKit holds a SameSite=Strict cookie back for the rest of that navigation.
        # This page sets it, then moves on by itself, from this site, so the cookie goes along.
        go = "/"  # the app opens on Your binder: it says what's next
        page_html = (f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" '
                     f'content="0;url={go}"><title>TaxBinder</title>')  # fmt: skip
        resp = HTMLResponse(page_html)
        resp.set_cookie(COOKIE, state.session_secret, httponly=True, samesite="strict", path="/")
        return resp

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    # -- pages ----------------------------------------------------------------------------

    def tax_year(year: int | None) -> int:
        return year or state.year or state.today().year - 1

    def pick_year(year: int | None) -> int:
        """Pages: a ?year= choice sticks (header switcher) until another one is made."""
        if year:
            state.year = year
        return tax_year(None)

    # -- start: create the vault, put its key back, restore a backup ------------

    @app.get("/start/{mode}", response_class=HTMLResponse)
    def start_form(request: Request, mode: str) -> Response:
        if state.store is not None or mode not in ("restore", "unlock"):
            return RedirectResponse("/", status_code=303)
        return start_page(request, mode)

    def opened(what: str) -> JSONResponse:
        """Open the vault just made, unlocked or restored (the caller holds the lock)."""
        state.open()
        if state.current_store() is None:
            return api_error(state.current_problem() or "the binder didn't open", 500)
        log.info("vault %s", what)
        return JSONResponse({"go": next_path(state.current_store())})

    @app.post("/api/start")
    def api_start() -> JSONResponse:
        """Get started: a new vault with no questions (its key into the Keychain)."""
        from taxbinder import start

        with state.lock:
            if state.problem_kind != "new":
                return api_error("there is a binder on this Mac already", 400)
            start.create(state.vault_path)
            return opened("created")

    @app.post("/api/start/unlock")
    def api_unlock(body: dict) -> JSONResponse:
        """Put the key back from the recovery key (the vault is still here)."""
        from taxbinder import start
        from taxbinder.store import WrongKey

        with state.lock:
            if state.current_store() is not None:  # the binder shown: yours or a filer's
                return api_error("your binder is open already", 400)
            try:
                start.unlock(state.vault_path, str(body.get("key") or ""))
            except FileNotFoundError:
                return api_error(
                    "There's no binder on this Mac to unlock. A recovery key alone can't bring "
                    "documents back: restore a backup instead.", 400)  # fmt: skip
            except WrongKey:
                return api_error("That recovery key doesn't open this binder. Nothing was "
                                 "changed.", 400)  # fmt: skip
            return opened("unlocked with the recovery key")

    @app.post("/api/start/retry")
    def api_retry() -> JSONResponse:
        """Try again: open the binders again once whatever held them has let go."""
        with state.lock:
            return opened("opened on Try again")

    @app.post("/api/start/restore")
    async def api_restore(request: Request) -> JSONResponse:
        """Restore from a backup: the .tbak file as the body, the recovery key in a header (never
        the URL). Only where there's no vault yet; a failure leaves things as they were."""
        import tempfile

        if state.problem_kind != "new":
            return api_error("there is a binder on this Mac already: nothing was changed", 400)
        key = request.headers.get("x-recovery-key", "")
        with tempfile.NamedTemporaryFile(suffix=".tbak") as f:
            async for chunk in request.stream():
                f.write(chunk)
            f.flush()
            if not f.tell():
                return api_error("choose a backup file (.tbak)", 400)
            return await asyncio.to_thread(restore_from, Path(f.name), key)

    def restore_from(file: Path, key: str) -> JSONResponse:
        """In a worker thread, under the lock (never held across an await)."""
        from taxbinder import start
        from taxbinder.store import WrongKey

        with state.lock:
            try:
                manifest = start.restore(file, key, state.vault_path)
            except (ValueError, FileExistsError, WrongKey) as e:
                return api_error(f"{str(e)[:1].upper()}{str(e)[1:]}. Nothing was changed.", 400)
            except OSError:
                log.exception("restore failed")
                return api_error("The backup couldn't be unpacked (is the disk full?). "
                                 "Nothing was changed.", 500)  # fmt: skip
            return opened(f"restored from a backup of {manifest.get('created', '')[:10]}")

    def year_starts(store: RecordStore) -> dict[str, Any]:
        """What the Start-a-year dialogs need (`_years.html`): the next year, if its rule
        parameters exist, from this year's rows; an earlier one from the first year's rows."""
        type_labels = doctypes.labels(store)
        years = years_in_vault(store)
        next_year = (max(years) + 1) if years else None
        try:
            load_params(next_year or 0)  # offer a year only when its rule parameters exist
        except ValueError:
            next_year = None
        carry = views.carry_over_rows(store, carry_over(store, next_year, next_year - 1),
                                      type_labels) if next_year else []  # fmt: skip
        # an earlier year starts from the first one's rows, each kept unless unticked
        first = min(years) if years else None
        earlier = list(range(first - 1, state.today().year - 16, -1)) if first else []
        back = [CarryOver(c.row, keep=True, why="") for c in carry_over(store, first - 1, first)
                ] if earlier else []  # fmt: skip
        return {"next_year": next_year, "carry": carry, "first_year": first, "earlier": earlier,
                "carry_back": views.carry_over_rows(store, back, type_labels),
                "rules_from": min(checked_years(), default=None)}  # fmt: skip

    @app.post("/api/results/answer")
    async def api_results_answer(request: Request) -> JSONResponse:
        """A question on Results answered No or Not sure: remembered for the year, undoable."""
        body = await request.json()
        actor = request.state.actor

        def work(store: RecordStore) -> dict:
            y = tax_year(body.get("year"))
            results.answer(store, y, str(body.get("kind", "")), str(body.get("value", "")), actor)
            return {"year": y}

        return act(work)

    @app.post("/api/add")
    async def api_add(request: Request) -> JSONResponse:
        """One file for the queue (raw body, name in X-Filename): checked and queued at once,
        read in the background. A bad file is reported in the batch, not as an error."""
        raw = request.headers.get("x-filename")
        batch = request.headers.get("x-batch", "")[:40]
        slug = state.current_entity()  # an open binder needs no lock: queuing only writes its
        store = state.store if slug == HOUSEHOLD else state._entity_stores.get(slug)  # inbox,
        if store is None:  # so a drop isn't held up while the reader checks a year
            with state.locked_store() as store:
                if store is None:
                    return api_error(state.current_problem() or "no vault", 503)
        if int(request.headers.get("content-length") or 0) > add.MAX_BYTES:  # not read at all
            item = reader.reject(Path(unquote(raw or "")).name, "is larger than 50 MB", batch)
        else:
            data = await request.body()
            ext = add.KINDS.get(sniff_mime(data))
            name = safe_upload_name(raw, ext) if ext else Path(unquote(raw or "")).name
            item = await asyncio.to_thread(reader.add, store, name or "file", data,
                                           request.state.actor, batch)  # fmt: skip
        return JSONResponse({"state": item.state, "note": item.note})

    # -- the demo (demo.py) --------------------------------------------------------

    @app.post("/api/demo")
    async def api_demo(request: Request) -> JSONResponse:
        """Try with sample data ("start"), Start the demo over ("over"), Leave the demo."""
        from taxbinder import demo

        what = str((await request.json()).get("action", ""))
        if what not in ("start", "over", "leave"):
            return api_error("start, over or leave", 400)

        if reader.batch.running:  # never switch binders half-way through reading files
            return api_error("Wait until the documents being read are done, then try again.", 409)

        def work() -> None:
            with state.lock:
                if what == "leave":
                    state.leave_demo()
                else:
                    state.enter_demo(fresh=what == "over")
            reader.clear()  # the Add page belongs to the binder that's open

        try:
            await asyncio.to_thread(work)
        except FileNotFoundError:
            return api_error("The sample documents aren't in this copy of TaxBinder.", 500)
        log.info("demo: %s", what)
        with state.locked_store() as store:
            go = "/add" if state.demo else next_path(store)
        return JSONResponse({"demo": state.demo, "go": go, "samples": demo.samples().is_dir()})

    @app.post("/api/demo/add")
    def api_demo_add(request: Request) -> JSONResponse:
        """Add the sample documents, as if dropped on Add documents (read, then Check)."""
        from taxbinder import demo

        if not state.demo:
            return api_error("only in the demo", 400)
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
        batch = secrets.token_hex(8)
        for p in demo.documents():
            reader.add(store, p.name, p.read_bytes(), request.state.actor, batch)
        return JSONResponse({"queued": len(demo.documents())})

    @app.get("/api/add/status")
    def api_add_status() -> JSONResponse:
        """Progress for the Add documents page (no lock: it's the reader's own list)."""
        return JSONResponse(reader.batch.summary())

    @app.get("/check", response_class=HTMLResponse)
    def check_page(request: Request, year: int | None = None) -> HTMLResponse:
        """Check: "Here's what we found": the household box, questions, looks right."""
        from taxbinder import check

        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            y = pick_year(year)
            house = home.worked_out(store, y)
            household = entity_info(store).form in ("1040", "1040-SR")
            found = check.report(store)
            later = any(not d.waiting for d in store.list("document"))  # filed before
            people = {t.id: t for t in store.list("taxpayer")}
            institutions = sorted(store.list("institution"), key=lambda i: i.name.lower())
            in_use = {s.institution for s in store.list("source")}
            here = state.current_entity()
            others = [(slug, info) for slug, info in state.entity_choices() if slug != here]
            if not settings.other_filers_on(state.store, state.vault_path):
                others = []
            type_labels = doctypes.labels(store)
        return page(
            request,
            "check.html",
            year=y,
            house=house,
            household=household,
            house_data={
                "people": [asdict(p) for p in house.people],
                "filing_status": house.filing_status,
                "state": house.state,
            },
            filing_statuses=returns.FILING_STATUSES,
            state_names=returns.STATES,
            found=found,
            people=people,
            institutions=institutions,
            mergeable={i.id for i in institutions} - in_use,
            others=others,
            labels=type_labels,
            house_open=household and not house.confirmed,
            later=later,
        )

    @app.post("/api/check/mark")
    async def api_check_mark(request: Request) -> JSONResponse:
        """An answer that's a mark: not a tax document, they're different, looks right, skip."""
        from taxbinder import check

        actor, body = request.state.actor, await request.json()
        return act(lambda store: check.mark(store, str(body.get("id", "")),
                                            str(body.get("what", "")), actor) or {})  # fmt: skip

    @app.post("/api/check/edit")
    async def api_check_edit(request: Request) -> JSONResponse:
        """Change what was read: type and year, who sent it, the account number."""
        from taxbinder import check

        actor, body = request.state.actor, await request.json()

        def work(store: RecordStore) -> dict:
            doc = str(body.get("id", ""))
            with store.operation(actor, f"check: change {doc}"):  # one Undo for all of it
                return edit(store, doc)

        def edit(store: RecordStore, doc: str) -> dict:
            if body.get("back"):  # changed on a Not filed row: back with the others
                check.mark(store, doc, "back", actor)
            if "doc_type" in body or "tax_year" in body:
                raw = str(body.get("tax_year") or "").strip()
                if raw and not raw.isdigit():
                    raise ValueError("give a tax year like 2025")
                now = store.get("document", doc)[0].classification
                doc_type = str(body.get("doc_type") or "") or (now.doc_type if now else None)
                year = int(raw) if raw else (now.tax_year if now else None)
                review.edit_document(store, doc, actor, doc_type, year)
            if "issuer" in body or "account" in body:
                check.edit(store, doc, actor,
                           str(body["issuer"]) if "issuer" in body else None,
                           str(body["account"]) if "account" in body else None)  # fmt: skip
            return {"id": doc}

        return act(work)

    @app.post("/api/check/box")
    async def api_check_box(request: Request) -> JSONResponse:
        """A key box's amount, as the person confirmed it: the rules', the model's, or
        typed."""
        from taxbinder import check

        actor, body = request.state.actor, await request.json()

        def work(store: RecordStore) -> dict:
            doc, box = str(body.get("id", "")), str(body.get("box", ""))
            check.confirm_box(store, doc, box, str(body.get("value", "")), actor)
            return {"id": doc, "box": box}

        return act(work)

    @app.post("/api/check/merge")
    async def api_check_merge(request: Request) -> JSONResponse:
        from taxbinder import check

        actor, body = request.state.actor, await request.json()
        old, into = str(body.get("from", "")), str(body.get("into", ""))
        return act(lambda store: {"moved": check.merge(store, old, into, actor)})

    @app.post("/api/check/confirm")
    def api_check_confirm(request: Request) -> JSONResponse:
        """Looks good, file them: one change in this vault, undone on its own."""
        from taxbinder import check

        actor = request.state.actor

        def work(store: RecordStore) -> dict:
            filed = check.confirm(store, actor)
            years = sorted({*years_in_vault(store), tax_year(None)})
            for y in years[-3:]:
                refresh(store, y, actor)
            return {
                "filed": filed,
                "go": "/backup" if keep_it_safe_next() else "/",
            }

        return act(work)

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request) -> HTMLResponse:
        """Not tax advice, the privacy statement, About and the help topics;
        open before a vault too."""
        from importlib.metadata import version

        listed = helptext.licences_file()
        return page(request, "help.html", app_version=version("taxbinder"),
                    vault=state.vault_path, inbox=state.vault_path / "inbox",
                    folder=state.downloads_dir,
                    where=helptext.places(state.vault_path),
                    licences=helptext.licence_count(listed) if listed else 0)  # fmt: skip

    @app.get("/api/diagnostics")
    def api_diagnostics() -> JSONResponse:
        """Copy diagnostic info: counts and recent errors, never values or file names."""
        refs = list_entities(state.vault_path)
        with state.lock:
            stores = [s for r in refs if (s := state.store_for(r.slug)) is not None]
            text = diagnostics.report(stores, diagnostics.formats_of([r.path for r in refs]),
                                      len(refs), state.sharing is not None)  # fmt: skip
        return JSONResponse({"text": text})

    @app.get("/help/licences", response_class=HTMLResponse)
    def licences_page(request: Request) -> HTMLResponse:
        """The third-party licences file made when the app was built, as it is."""
        path = helptext.licences_file()
        return page(request, "licences.html", text=path.read_text() if path else None)

    @app.get("/preparer", response_class=HTMLResponse)
    def preparer_page(request: Request, year: int | None = None) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        from taxbinder.package import handoff

        with state.locked_store() as store:
            y = pick_year(year)
            status = year_status(store, y, state.today())
            answers = results.answers(store, y, state.today(), views.checklist_rows(store, y),
                                      status)  # fmt: skip
            made = handoff.last(store, y)
        return page(request, "preparer.html", year=y, a=answers, made=made,
                    folder=state.downloads_dir)  # fmt: skip

    @app.post("/api/preparer/save")
    async def api_preparer_save(request: Request) -> JSONResponse:
        """The .zip for the preparer into this Mac's Downloads folder; never sent."""
        from taxbinder.package import handoff

        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            path, result = handoff.make(store, int(body.get("year", 0)), state.downloads_dir, actor)
            log.info("files for the preparer saved to Downloads by %s", actor)
            return {"name": path.name, "count": sum(result.files.values()),
                    "unreadable": len(result.unreadable)}  # fmt: skip

        return act(work)

    def is_return(doc: Any) -> bool:  # noqa: ANN401
        c = doc.classification
        return bool(c and c.doc_type in returns.RETURN_TYPES)

    def sender(doc: Any, names: dict[str, str]) -> str:  # noqa: ANN401
        """'Larkmoor Securities LLC · …3017': who sent it, and the account's last 4 only."""
        c = doc.classification
        if c is None:
            return ""
        digits = re.sub(r"\D", "", c.account_number or "")
        ending = f"…{digits[-4:]}" if len(digits) >= 4 else ""
        return " · ".join(x for x in (names.get(c.issuer or "", ""), ending) if x)

    def in_year(doc: Any, records: list[str] | None, year: int) -> bool:  # noqa: ANN401
        """A file belongs to the years of what it's attached to; an unattached one to the year
        read from it, or to every year when none could be read (so it isn't lost)."""
        if records:
            return any(r.startswith(f"{year}/") for r in records)
        read = doc.classification.tax_year if doc.classification else None
        return read is None or read == year

    def returns_section(store: RecordStore, year_returns: list, y: int) -> dict:
        reads = {}  # a 1040's contents, against this year's and next year's checklist
        for r in year_returns:
            if r.info.read is not None:
                now, later = (
                    returns.listed_for(store, r.info, y),
                    returns.listed_for(store, r.info, y + 1),
                )
                reads[r.doc.id] = {
                    "read": r.info.read,
                    "listed": now,
                    "add_now": sum(s.row is None and not s.here for s in now),
                    "add_next": sum(s.row is None for s in later),
                }
        return {
            "returns": year_returns,
            "reads": reads,
            "state_names": returns.STATES,
            "paper_names": returns.PAPER_NAMES,
            "filing_statuses": returns.FILING_STATUSES,
            "totals_only": returns.TOTALS_ONLY,
        }

    @app.get("/documents", response_class=HTMLResponse)
    def documents(request: Request, year: int | None = None, all: bool = False) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        y = pick_year(year)
        here = state.current_entity()
        with state.locked_store() as store:
            docs = sorted(store.list("document"), key=lambda d: d.acquired.at, reverse=True)
            type_labels = doctypes.labels(store)
            targets = sorted(
                r.id for r in [*store.list("expectation", y), *store.list("claim", y)]
            )  # what a file can be attached to this year
            linked: dict[str, list[str]] = {}  # doc id → rows and claims it's evidence for
            for record in [*store.list("expectation"), *store.list("claim")]:
                for x in record.evidence:
                    linked.setdefault(x.doc, []).append(record.id)
            section = returns_section(store, returns.returns_for_year(store, y), y)
            names = {i.id: i.name for i in store.list("institution")}
            sent_by = {d.id: sender(d, names) for d in docs}
            people = {t.id: t for t in store.list("taxpayer")}
            for_whom = {d.id: whose.label(d.recipient, people) for d in docs}
            replaced = {d.version.supersedes for d in docs if d.version.supersedes}
            others = [(slug, info) for slug, info in state.entity_choices() if slug != here]
            if not settings.other_filers_on(state.store, state.vault_path):
                others = []  # other filers are off (Settings): no "Move to …'s vault"
            movable = {}  # doc id → (slug, name): in the name of a filer with its own vault
            for d in docs:
                if d.recipient and d.recipient.taxpayers and others:
                    named = [people[t].name for t in d.recipient.taxpayers if t in people]
                    if slug := filer_for(named, others):
                        movable[d.id] = (slug, dict(others)[slug].name)
            counts = Counter(d.classification.doc_type for d in docs if d.classification)
            listed = {e.year for e in store.list("expectation")}  # years with a checklist
            own_returns = [  # an added type that's probably the person's name for returns
                (t, counts[t.id])
                for t in doctypes.custom_types(store)
                if doctypes.looks_like_return(t) and counts[t.id]
            ]
        total = len(docs)
        seen = {y} | listed | {int(r.split("/")[0]) for rs in linked.values() for r in rs
                      if r.split("/")[0].isdigit()}  # fmt: skip
        seen |= {d.classification.tax_year for d in docs
                 if d.classification and d.classification.tax_year}  # fmt: skip
        year_counts = [  # the year menu: every year with documents, newest first
            (x, sum(1 for d in docs if in_year(d, linked.get(d.id), x)))
            for x in sorted(seen, reverse=True)
        ]
        if not all:
            docs = [d for d in docs if in_year(d, linked.get(d.id), y)]
        unmatched = sum(1 for d in docs if d.id not in linked and not is_return(d)
                        and not preparer.is_paper(d))  # fmt: skip
        return page(
            request,
            "documents.html",
            docs=docs,
            labels=type_labels,
            unmatched=unmatched,
            linked=linked,
            year=y,
            all_years=all,
            total=total,
            year_counts=year_counts,
            targets=targets,
            own_returns=own_returns,
            sent_by=sent_by,
            for_whom=for_whom,
            issuer_of={d.id: names.get(d.classification.issuer or "", "")
                       for d in docs if d.classification},  # fmt: skip
            acct_of={d.id: re.sub(r"\D", "", d.classification.account_number or "")[-4:]
                     for d in docs if d.classification},  # the last 4 only  # fmt: skip
            senders=sorted(set(names.values()), key=str.lower),
            people=sorted(people.values(), key=lambda t: (t.kind != "individual", t.name)),
            replaced=replaced,
            movable=movable,
            **section,
        )

    # -- API ------------------------------------------------------------------------------

    @app.get("/api/status")
    def api_status() -> dict:
        with state.locked_store() as store:
            count = len(store.list("document")) if store else None
        return {
            "vault": str(state.vault_path),
            "ready": state.store is not None,
            "documents": count,
        }

    @app.post("/api/open-portal")
    async def open_portal(request: Request) -> JSONResponse:
        body = await request.json()
        url = str(body.get("url", "")).strip()
        if err := validate_portal_url(url):
            return JSONResponse({"error": err}, status_code=400)
        if state.demo:  # no real sign-ins with an invented household
            return api_error("Websites don't open in the demo. Leave the demo first.", 400)
        with state.lock:
            in_browser = settings.load(state.store).websites_open_in == "browser"
        if in_browser:  # the person's choice (Settings): no app window, they save files themselves
            state.open_url(url)
        else:
            state.emit({"type": "open_portal", "url": url, "label": body.get("label")})
        return JSONResponse({"requested": True, "browser": in_browser}, status_code=202)

    @app.post("/api/capture-page")
    def capture_page() -> JSONResponse:
        """Ask the shell to save whatever the last-used portal window is showing."""
        state.emit({"type": "capture_page"})
        return JSONResponse({"requested": True}, status_code=202)

    @app.post("/api/print")
    def print_page() -> JSONResponse:
        """Ask the shell to open the Mac's print panel for the app window (Save as PDF is in it)."""
        if not state.in_app:
            return api_error("printing from here is your browser's Print command", 400)
        state.emit({"type": "print"})
        return JSONResponse({"requested": True}, status_code=202)

    # -- actions (first writes from the UI; same guard as above) -----------------------

    def api_error(message: str, status_code: int) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status_code)

    def after_reading(store: RecordStore, years: set[int], matched: set[str]) -> None:
        """Once the Add documents queue is read: the year being worked on checked too (each
        document's own year was, as it was read), and the model reader woken for anything
        left."""
        if tax_year(None) not in years:
            refresh(store, tax_year(None), "auto:ingest")
        if state.model_reader is not None:
            state.model_reader.wake()

    def each_document(store: RecordStore, doc_id: str, year: int | None, item: add.Item) -> None:
        """One new document's last steps, each reported as it's reached: its amounts
        read by the rules (suggestions only), the model's reading, the checks for its year."""
        with state.lock:
            counts = matrix.read_pdfs(store, "auto:matrix", only_docs={doc_id})
        item.reach("amounts", amounts=counts["read"], suggested=counts["suggested"])
        model = state.model_reader
        item.reach("model", model=model.read_now(store, doc_id) if model else {"state": "no model"})
        with state.lock:
            checks = refresh(store, year, "auto:ingest") if year else {}
        item.reach("checks", checks=checks)

    reader = add.Reader(state, after_reading, each_document)
    app.state.reader = reader  # the sidecar wakes it at start, for files left last time

    def refresh(store: RecordStore, year: int, actor: str) -> dict:
        """Re-run the checks after a change; a year without rule parameters can't be checked."""
        try:
            reconcile(store, year, actor)
        except ValueError as e:
            return {"problem": str(e)}  # not a severity count
        return dict(year_status(store, year, state.today()).counts())

    def act(work: Callable[[RecordStore], dict], household: bool = False) -> JSONResponse:
        """Run one write under the store lock: unknown ids → 404, refused changes → 400. The
        reply names the change it made ("undo"), so the toast's Undo reverses exactly that.
        `household`: in the household's vault whichever filer is shown (the app's settings)."""
        with state.lock:
            store = state.store if household else state.current_store()
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                before = len(store.completed)
                result = work(store)
                made = [
                    op
                    for op in store.completed[before:]
                    if op.sha
                    and not op.message.startswith(
                        ("reconcile", "redaction:", "matrix: read figures", "matrix: accepted")
                    )
                ]
                if made:
                    result["undo"] = {"sha": made[-1].sha, "message": made[-1].message}
                return JSONResponse(result)
            except RecordNotFound as e:
                return api_error(f"not found: {e}", 404)
            except RevConflict:
                return api_error("changed by someone else since this page loaded; reload", 409)
            except ValueError as e:  # includes validation errors
                detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
                return api_error(detail, 400)

    @app.post("/api/year/new")
    async def api_year_new(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            year, from_year = int(body.get("year", 0)), int(body.get("from", 0))
            drop = body.get("drop")
            n = new_year(
                store, year, from_year, actor, None if drop is None else set(map(str, drop))
            )
            state.year = year
            return {"year": year, "rows": n, "counts": refresh(store, year, actor)}

        return act(work)

    @app.post("/api/rows")
    async def api_row_add(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            doc_type = str(body.get("doc_type", ""))
            year = tax_year(body.get("year"))
            exp_id = add_row(
                store,
                year,
                str(body.get("institution", "")),
                str(body.get("source", "")),
                doc_type,  # type: ignore[arg-type]
                str(body.get("owner", "")),
                actor,
                amount=parse_amount(str(body.get("amount") or "")),
                comment=str(body.get("comment") or ""),
            )
            return {"id": exp_id, "counts": refresh(store, year, actor)}

        return act(work)

    @app.post("/api/rows/edit")
    async def api_row_edit(request: Request) -> JSONResponse:
        """Inline edits from the checklist: amount and/or comment."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            changes: dict = {}
            if "amount" in body:
                changes["amount"] = parse_amount(str(body["amount"] or ""))
            if "comment" in body:
                changes["comment"] = str(body["comment"] or "")
            if "portal" in body:
                review.set_portal_url(
                    store, str(body.get("id", "")), str(body["portal"] or ""), actor
                )
            if changes:
                edit_row(store, str(body.get("id", "")), actor, **changes)
            exp, _ = store.get("expectation", str(body.get("id", "")))
            amount = f"{exp.amount:,.2f}" if exp.amount is not None else ""
            return {
                "id": exp.id,
                "amount": amount,
                "amount_raw": str(exp.amount or ""),
                "comment": exp.comment,
            }

        return act(work)

    @app.post("/api/doc-types")
    async def api_doc_type_add(request: Request) -> JSONResponse:
        """A new document type from the Add-a-row dialog."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            words = body.get("keywords") or []
            if isinstance(words, str):
                words = [w for w in words.split(",")]
            d = doctypes.add_doc_type(
                store, str(body.get("name", "")), str(body.get("folder") or ""), words, actor
            )
            return {"id": d.id, "name": d.name, "folder": d.folder, "keywords": d.keywords}

        return act(work)

    @app.post("/api/rows/delete")
    async def api_row_delete(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            exp, _ = store.get("expectation", str(body.get("id", "")))
            delete_row(store, exp.id, actor)
            return {"deleted": exp.id, "counts": refresh(store, exp.year, actor)}

        return act(work)

    @app.post("/api/rematch")
    def api_rematch(request: Request) -> JSONResponse:
        """Match again the documents linked to nothing (existing links never move)."""
        actor = request.state.actor

        def work(store: RecordStore) -> dict:
            results = rematch(store, actor)
            linked = [r for r in results if r.linked_to]
            for y in sorted({int(r.linked_to.split("/")[0]) for r in linked}):
                refresh(store, y, actor)
            tied = [
                r
                for r in results
                if not r.linked_to
                and len(r.candidates) > 1
                and r.candidates[0][1] == r.candidates[1][1]
            ]
            return {
                "checked": len(results),
                "linked": [{"doc_id": r.doc_id, "linked_to": r.linked_to} for r in linked],
                "tied": len(tied),  # fit several rows equally: attach with ••• → Edit…
            }

        return act(work)

    @app.post("/api/applicability")
    async def api_applicability(request: Request) -> JSONResponse:
        """From the app only: applicable, or not applicable (noted as decided in the app)."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            value = str(body.get("value", ""))
            if value not in ("applicable", "not_applicable"):
                raise ValueError("the app sets applicable or not_applicable only")
            exp, _ = store.get("expectation", str(body.get("id", "")))
            why = str(body.get("reason") or "").strip()[:200]  # from the deck card
            note = None if value == "applicable" else (
                f"{why} (marked in the app by {actor})" if why
                else f"marked not applicable in the app by {actor}")
            set_applicability(store, exp.id, value, note, actor)
            return {"id": exp.id, "value": value, "counts": refresh(store, exp.year, actor)}

        return act(work)

    @app.post("/api/verify")
    async def api_verify(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()
        exp_id, verified = str(body.get("id", "")), bool(body.get("verified", True))

        def work(store: RecordStore) -> dict:
            exp, _ = store.get("expectation", exp_id)
            note = "checked in the app" if verified else "undone in the app"
            set_verified(store, [exp_id], verified, note, actor)
            return {"id": exp_id, "verified": verified, "counts": refresh(store, exp.year, actor)}

        return act(work)

    @app.post("/api/reconcile")
    async def api_reconcile(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            counts = refresh(store, tax_year(body.get("year")), actor)
        if "problem" in counts:
            return api_error(counts["problem"], 400)
        return JSONResponse({"counts": counts})

    @app.post("/api/upload")
    async def api_upload(
        request: Request,
        year: int | None = None,
        expectation: str | None = None,
        target: str | None = None,
        as_type: str | None = None,
    ) -> JSONResponse:
        """Raw file body; name in X-Filename (URL-encoded). With `target` (a checklist row or a
        payment; `expectation` is the older name), the file is linked to it."""
        actor = request.state.actor
        expectation = target or expectation
        if as_type not in (*BUILTIN_DOC_TYPES, "none"):  # hint for unrecognised files
            as_type = None
        kind = "claim" if expectation and expectation.count("/") == 2 else "expectation"
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            return api_error("file is larger than 50 MB", 413)
        data = await request.body()
        if not data or len(data) > MAX_UPLOAD:
            return api_error("empty file" if not data else "file is larger than 50 MB", 413)
        ext = UPLOAD_TYPES.get(sniff_mime(data))
        if ext is None:
            return api_error("only PDF, PNG, JPEG, HEIC or saved HTML pages can be added", 415)
        name = safe_upload_name(request.headers.get("x-filename"), ext)
        # a scan or photo is read before the vault is locked, so the window never waits on it
        mime = sniff_mime(data)
        text = pdf_info(data)[1] if mime == "application/pdf" else ""
        pages = await asyncio.to_thread(ocr.read, data, mime) if needs_ocr(mime, text) else None

        def work(store: RecordStore) -> dict:
            if expectation:
                try:
                    record, _ = store.get(kind, expectation)
                except ValueError as e:  # not a row or claim id
                    raise RecordNotFound(f"{kind} {expectation!r}") from e
            path, n = store.vault.inbox / name, 2
            while path.exists():
                path, n = store.vault.inbox / f"{Path(name).stem}-{n}.{ext}", n + 1
            path.write_bytes(data)
            note, what = None, f"add {name}" + (f" to {expectation}" if expectation else "")
            with store.operation(actor, what):  # stored and attached as one: one Undo takes it off
                result = ingest_file(store, path, "upload", actor, pages=pages)
                if expectation and result.linked_to != expectation:
                    if not any(link.doc == result.doc_id for link in record.evidence):
                        link_document(
                            store,
                            result.doc_id,
                            expectation,
                            actor,
                            role="other" if as_type == "none" else "primary",
                            as_type=as_type,
                        )
                    if result.linked_to:
                        note = f"also matched automatically to {result.linked_to}"
            doc, _ = store.get("document", result.doc_id)
            c = doc.classification
            linked = [
                r.id
                for r in [*store.list("expectation"), *store.list("claim")]
                if any(x.doc == doc.id for x in r.evidence)
            ]
            years = {tax_year(year)} | ({c.tax_year} if c and c.tax_year else set())
            for y in sorted(years):
                counts = refresh(store, y, actor)
            if linked and not result.duplicate:
                matrix.read_pdfs(store, "auto:matrix", only_docs={doc.id})  # suggestions only
            log.info("upload ingested (%s)", "duplicate" if result.duplicate else "new")
            return {
                "doc_id": doc.id,
                "filename": doc.original_filename,
                "duplicate": result.duplicate,
                "doc_type": DOC_LABELS.get(c.doc_type, c.doc_type) if c and c.doc_type else None,
                "tax_year": c.tax_year if c else None,
                "linked_to": linked,
                "note": note,
                "counts": counts,
            }

        return act(work)

    # -- entities (entities.py) ---------------------------------------------------------

    @app.post("/api/entity")
    async def api_entity_select(request: Request) -> JSONResponse:
        """Work on another entity; the choice is this browser's (or the app window's) own."""
        body = await request.json()
        slug = str(body.get("slug", ""))
        if slug not in {ref.slug for ref in list_entities(state.vault_path)}:
            return api_error(f"no entity {slug!r}", 404)
        with state.lock:
            if state.store_for(slug) is None and state._entity_kinds.get(slug) != "nokey":
                return api_error(state._entity_problems.get(slug, "can't open it"), 503)
        state.selected[request.state.client] = slug
        return JSONResponse({"entity": slug})

    @app.post("/api/entity/rename")
    async def api_entity_rename(request: Request) -> JSONResponse:
        from taxbinder.entities import rename_entity

        body = await request.json()
        slug = str(body.get("slug", ""))
        if slug not in {ref.slug for ref in list_entities(state.vault_path)}:
            return api_error(f"no entity {slug!r}", 404)
        with state.lock:
            store = state.store_for(slug)
            if store is None:
                return api_error(state._entity_problems.get(slug, "can't open it"), 503)
            try:
                info = rename_entity(store, str(body.get("name", "")), request.state.actor)
            except ValueError as e:
                detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
                return api_error(detail, 400)
        return JSONResponse({"entity": slug, "name": info.name})

    @app.post("/api/entities")
    async def api_entity_add(request: Request) -> JSONResponse:
        """A new entity with its own vault (same keys as the household's)."""
        from taxbinder.entities import create_entity
        from taxbinder.store import keys_from_env

        body = await request.json()
        try:
            slug = create_entity(
                state.vault_path,
                keys_from_env(),
                str(body.get("name", "")),
                str(body.get("form", "")),
                request.state.actor,
            )
        except (ValueError, OSError) as e:
            return api_error(str(e), 400)
        state.selected[request.state.client] = slug
        return JSONResponse({"entity": slug})

    # -- history and undo ----------------------------------------------------------------

    def undoable(changes: list[Any]) -> dict[str, str | None]:
        """sha → None if it can be undone, else why not (same rule as RecordStore.undo)."""
        out: dict[str, str | None] = {}
        for i, c in enumerate(changes):
            paths = {p for p in c.paths if p != "vault.yaml" and not p.endswith("findings.yaml")}
            if c.parent is None or not paths:
                out[c.sha] = "nothing to undo"
                continue
            later = next((x for x in live_changes(changes[:i]) if paths & set(x.paths)), None)
            out[c.sha] = f"changed again by “{later.message}”" if later else None
        return out

    @app.get("/history", response_class=HTMLResponse)
    def history_page(request: Request, all: int = 0) -> HTMLResponse:  # noqa: A002
        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            changes = store.vault.history.changes(limit=200) if store.vault.crypto else []
        reasons = undoable(changes)
        shown = [c for c in changes if all or not c.automatic][:100]
        return page(request, "history.html", changes=shown, reasons=reasons, show_all=bool(all))

    @app.post("/api/undo")
    async def api_undo(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            sha = str(body.get("sha", ""))
            years = store.undo(sha, actor)
            for y in sorted(years | {tax_year(None)}):
                refresh(store, y, actor)
            return {"undone": sha}

        return act(work)

    # -- donations (donations.py) ----------------------------------------------------------

    @app.get("/donations", response_class=HTMLResponse)
    def donations_page(request: Request, year: int | None = None) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        y = pick_year(year)
        with state.locked_store() as store:
            rows = gifts.donation_rows(store, y)
            charities = sorted({c.payee for c in store.list("claim") if c.category == "donation"})
        return page(
            request,
            "donations.html",
            rows=rows,
            totals=gifts.summary(rows),
            year=y,
            kinds=gifts.DONATION_KINDS,
            charities=charities,
            today=state.today(),
        )

    @app.post("/api/donations")
    async def api_donation_add(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            def day(key: str, what: str, required: bool = True) -> date | None:
                raw = str(body.get(key) or "")
                if not raw and not required:
                    return None
                try:
                    return date.fromisoformat(raw)
                except ValueError:
                    raise ValueError(f"give the {what}") from None

            claim_id = gifts.add_donation(
                store,
                str(body.get("payee", "")),
                parse_amount(str(body.get("amount") or "")),  # type: ignore[arg-type]
                day("date", "date of the gift"),  # type: ignore[arg-type]
                str(body.get("kind") or "cash"),
                actor,
                to_daf=bool(body.get("to_daf")),
                security=str(body.get("security") or ""),
                shares=parse_amount(str(body.get("shares") or "")),
                cost_basis_per_share=parse_amount(str(body.get("cost_basis") or "")),
                acquired=day("acquired", "date the shares were acquired", required=False),
                quid_pro_quo=bool(body.get("quid_pro_quo")),
            )
            return {"id": claim_id, "counts": refresh(store, int(claim_id[:4]), actor)}

        return act(work)

    @app.post("/api/donations/edit")
    async def api_donation_edit(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            fields: dict[str, Any] = {}
            for key in ("payee", "kind", "security"):
                if key in body:
                    fields[key] = str(body[key] or "")
            for key in ("to_daf", "quid_pro_quo"):
                if key in body:
                    fields[key] = bool(body[key])
            if "amount" in body:
                fields["amount"] = parse_amount(str(body["amount"] or ""))
            if "shares" in body:
                fields["shares"] = parse_amount(str(body["shares"] or ""))
            if "cost_basis" in body:
                fields["cost_basis_per_share"] = parse_amount(str(body["cost_basis"] or ""))
            for key, what in (("date", "date of the gift"), ("acquired", "acquired date")):
                if body.get(key):
                    fields[key] = _date(body[key], what)
                elif key == "acquired" and key in body:
                    fields[key] = None
            old = str(body.get("id", ""))
            new = gifts.edit_donation(store, old, actor, **fields)
            for y in sorted({int(old[:4]), int(new[:4])}):
                refresh(store, y, actor)
            return {"id": new}

        return act(work)

    @app.post("/api/donations/delete")
    async def api_donation_delete(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            claim, _ = store.get("claim", str(body.get("id", "")))
            if claim.category != "donation":
                raise ValueError("only donations can be deleted here")
            delete_claim(store, claim.id, actor)
            return {"deleted": claim.id, "counts": refresh(store, claim.year, actor)}

        return act(work)

    # -- foreign accounts (foreign.py) ---------------------------------------------

    @app.get("/foreign", response_class=HTMLResponse)
    def foreign_page(request: Request, year: int | None = None) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        y = pick_year(year)
        with state.locked_store() as store:
            rows, people, line = foreign.accounts(store, y)
            carry = foreign.carry(store, y)
            banks = sorted({i.name for i in store.list("institution")})
            taxpayers = store.list("taxpayer")
        return page(request, "foreign.html", rows=rows, people=people, line=line, carry=carry,
                    banks=banks, taxpayers=taxpayers, year=y)  # fmt: skip

    @app.post("/api/foreign")
    async def api_foreign(request: Request) -> JSONResponse:
        """`what`: "add" (the default), "edit" or "delete" an account abroad."""
        actor = request.state.actor
        body = await request.json()
        what = body.get("what") or "add"

        def amount(key: str):  # noqa: ANN202 - a Decimal, or None when empty
            return parse_amount(str(body.get(key) or ""))

        def work(store: RecordStore) -> dict:
            if what == "add":
                y = tax_year(int(body.get("year") or 0) or None)
                claim_id = foreign.add_account(
                    store, y, str(body.get("bank") or ""), str(body.get("account") or ""),
                    str(body.get("owner") or ""), str(body.get("currency") or ""),
                    amount("max_balance"), actor, interest=amount("interest"), rate=amount("rate"),
                    source=str(body.get("source") or "") or None,
                )  # fmt: skip
                return {"id": claim_id, "counts": refresh(store, y, actor)}
            claim, _ = store.get("claim", str(body.get("id", "")))
            if claim.category != "fbar":
                raise ValueError("only foreign accounts can be changed here")
            if what == "edit":
                foreign.edit_account(
                    store, claim.id, actor, currency=str(body.get("currency") or ""),
                    max_balance=amount("max_balance"), interest=amount("interest"),
                    rate=amount("rate"),
                )  # fmt: skip
            elif what == "delete":
                delete_claim(store, claim.id, actor)
            else:
                raise ValueError(f"unknown change {what!r}")
            return {"id": claim.id, "counts": refresh(store, claim.year, actor)}

        return act(work)

    # -- payments (claims.py) ------------------------------------------------------------

    @app.get("/payments", response_class=HTMLResponse)
    def payments_page(request: Request, year: int | None = None) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        y = pick_year(year)
        with state.locked_store() as store:
            rows, totals = payment_rows(store, y)
            upcoming = [payment_row(c) for c in bills.upcoming(store)]
            try:
                resident = store.get("year", str(y))[0].resident_states
            except RecordNotFound:
                resident = []
        try:
            params = load_params(y)
        except ValueError:
            params = None
        return page(
            request,
            "payments.html",
            rows=rows,
            totals=totals,
            missing=sum(r.confirmation[0] == "missing" for r in rows),
            upcoming=upcoming,
            today=state.today(),
            bill_kinds=bills.BILL_KINDS,
            year=y,
            kinds=PAYMENT_KINDS,
            due_dates=states.due_dates(y, resident, params),
            resident=resident,
            states_known=bool(resident),
        )

    @app.post("/api/payments")
    async def api_payment_add(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            try:
                day = date.fromisoformat(str(body.get("date", "")))
            except ValueError:
                raise ValueError("give the date the payment was made") from None
            amount = parse_amount(str(body.get("amount") or ""))
            category = str(body.get("category") or "tax_payment")
            if category == "tax_payment":
                claim_id = add_payment(
                    store,
                    str(body.get("jurisdiction", "")),
                    amount,  # type: ignore[arg-type]
                    day,
                    str(body.get("kind", "")),
                    actor,
                    int(body["year"]) if body.get("year") else None,
                )
            else:  # property tax, vehicle / DMV, anything else: recorded as paid on that date
                if amount is None:
                    raise ValueError("give the amount paid")
                [claim_id] = bills.add_bill(
                    store,
                    category,
                    str(body.get("payee", "")),
                    [(str(body.get("label") or ""), amount, day)],
                    actor,
                    paid=True,
                )
            return {"id": claim_id, "counts": refresh(store, int(claim_id[:4]), actor)}

        return act(work)

    @app.post("/api/bills")
    async def api_bill_add(request: Request) -> JSONResponse:
        """A bill and its installments, each a planned payment (TaxBinder never pays)."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            installments = []
            for n, item in enumerate(body.get("installments") or [], start=1):
                try:
                    due = date.fromisoformat(str(item.get("due", "")))
                except ValueError:
                    raise ValueError(f"installment {n}: give its due date") from None
                amount = parse_amount(str(item.get("amount") or ""))
                if amount is None:
                    raise ValueError(f"installment {n}: give its amount")
                installments.append((str(item.get("label") or ""), amount, due))
            ids = bills.add_bill(
                store,
                str(body.get("kind", "")),
                str(body.get("payee", "")),
                installments,
                actor,
                bill_doc=str(body["doc_id"]) if body.get("doc_id") else None,
                jurisdiction=str(body.get("jurisdiction") or ""),
                payment_kind=str(body.get("payment_kind") or ""),
            )
            for y in sorted({int(i[:4]) for i in ids}):
                refresh(store, y, actor)
            return {"ids": ids}

        return act(work)

    def _date(value: Any, what: str) -> date:  # noqa: ANN401
        try:
            return date.fromisoformat(str(value or ""))
        except ValueError:
            raise ValueError(f"give the {what}") from None

    @app.post("/api/bills/schedule")
    async def api_bill_schedule(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            claim_id = str(body.get("id", ""))
            bills.schedule(
                store,
                claim_id,
                _date(body.get("pay_on"), "date it's scheduled for"),
                str(body.get("note") or ""),
                actor,
            )
            return {"id": claim_id, "counts": refresh(store, int(claim_id[:4]), actor)}

        return act(work)

    @app.post("/api/bills/paid")
    async def api_bill_paid(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            amount = parse_amount(str(body.get("amount") or ""))
            new_id = bills.mark_paid(
                store,
                str(body.get("id", "")),
                _date(body.get("paid_on"), "date it was paid"),
                actor,
                amount,
            )
            for y in sorted({int(new_id[:4]), int(str(body.get("id", "0000"))[:4])}):
                refresh(store, y, actor)
            return {"id": new_id}

        return act(work)

    def _event(store: RecordStore, claim_id: str) -> tuple[str, str]:
        claim, _ = store.get("claim", claim_id)
        if claim.plan is None:
            raise ValueError("only planned payments go in the calendar")
        name = re.sub(
            r"[^\w .-]", "_", f"Pay {claim.plan.payee} {claim.plan.pay_on or claim.plan.due}"
        )
        return bills.calendar_event(claim), f"{name}.ics"

    @app.get("/payments/calendar")
    def bill_calendar(request: Request, id: str = "") -> Response:  # noqa: A002
        """A browser: download the event; Safari / Calendar adds it."""
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                text, name = _event(store, id)
            except (RecordNotFound, ValueError) as e:
                return api_error(str(e) if isinstance(e, ValueError) else "no such payment", 404)
        return attachment(text.encode(), name, "text/calendar")

    @app.post("/api/bills/calendar")
    async def api_bill_calendar(request: Request) -> JSONResponse:
        """The app window: save the event into Downloads and open it (Calendar asks to add it)."""
        body = await request.json()

        def work(store: RecordStore) -> dict:
            text, name = _event(store, str(body.get("id", "")))
            state.downloads_dir.mkdir(parents=True, exist_ok=True)
            path = downloads.unique_path(state.downloads_dir, name)
            path.write_text(text)
            state.open_file(path)
            return {"saved": path.name}

        return act(work)

    @app.post("/api/payments/edit")
    async def api_payment_edit(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            old = str(body.get("id", ""))
            new = edit_payment(
                store,
                old,
                actor,
                day=_date(body["date"], "date") if body.get("date") else None,
                amount=parse_amount(str(body.get("amount") or "")),
                payee=str(body["payee"]) if "payee" in body else None,
                label=str(body["label"]) if "label" in body else None,
            )
            for y in sorted({int(old[:4]), int(new[:4])}):
                refresh(store, y, actor)
            return {"id": new}

        return act(work)

    @app.post("/api/payments/delete")
    async def api_payment_delete(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            claim, _ = store.get("claim", str(body.get("id", "")))
            if claim.category not in PAYMENT_CATEGORIES:
                raise ValueError("only payments and bills can be deleted here")
            delete_claim(store, claim.id, actor)
            return {"deleted": claim.id, "counts": refresh(store, claim.year, actor)}

        return act(work)

    # -- sharing on the home network (sharing.py) ----------------------------------------

    def open_pairing() -> lan.Pairing | None:
        p = state.pairing
        return p if p is not None and p.tries_left > 0 and p.expires > lan.now() else None

    @app.get("/sharing", response_class=HTMLResponse)
    def sharing_page(request: Request) -> HTMLResponse:
        return page(request, "sharing.html", pairing=open_pairing(), devices=state.devices.items)

    @app.post("/api/sharing")
    async def api_sharing(request: Request) -> JSONResponse:
        body = await request.json()
        if body.get("on") and state.demo:
            return api_error("The demo isn't shared on the network. Leave the demo first.", 400)
        if body.get("on"):
            if state.sharing is None:
                if state.store is None:
                    return api_error(state.current_problem() or "no vault", 503)
                try:
                    port = lan.configured_port()
                    state.sharing = lan.start(app, state.vault_path / "private", port)
                except OSError as e:
                    return api_error(f"can't listen on port {port}: {e.strerror}", 400)
            return JSONResponse({"on": True, "address": state.sharing.address})
        if state.sharing is not None:
            state.sharing.stop()
            state.sharing, state.pairing = None, None
        return JSONResponse({"on": False})

    @app.post("/api/pairing")
    def api_pairing() -> JSONResponse:
        if state.sharing is None:
            return api_error("turn on sharing first", 400)
        state.pairing = lan.Pairing.new()
        return JSONResponse(
            {"code": state.pairing.code, "expires": state.pairing.expires.isoformat()}
        )

    @app.post("/api/devices/revoke")
    async def api_revoke(request: Request) -> JSONResponse:
        body = await request.json()
        if not state.devices.revoke(str(body.get("id", ""))):
            return api_error("no such device", 404)
        return JSONResponse({"revoked": body["id"]})

    @app.get("/pair", response_class=HTMLResponse)
    def pair_page(request: Request) -> Response:
        if state.devices.check(request.cookies.get(lan.DEVICE_COOKIE)):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "pair.html", {"remote": True})

    @app.post("/api/pair")
    async def api_pair(request: Request) -> JSONResponse:
        """A device on the network proves it was given the code shown on this Mac."""
        body = await request.json()
        pairing = open_pairing()
        if pairing is None:
            state.pairing = None
            return api_error("no pairing is open; ask for a new code on the Mac", 400)
        result = pairing.try_code(str(body.get("code", "")))
        if result == "wrong":
            return api_error(f"wrong code; {pairing.tries_left} tries left", 400)
        if result == "closed":
            state.pairing = None
            return api_error("this code can't be used any more; ask for a new one", 400)
        device, token = state.devices.add(str(body.get("name", "")))
        state.pairing = None
        log.info("device paired")
        resp = JSONResponse({"paired": device.name})
        resp.set_cookie(
            lan.DEVICE_COOKIE,
            token,
            max_age=400 * 24 * 3600,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return resp

    # -- downloads (downloads.py) ---------------------------------------------------------

    def attachment(data: bytes | Any, name: str, media_type: str) -> Response:  # noqa: ANN401
        """Always a download, never shown inside the app (a saved web page could run scripts)."""
        ascii_name = name.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
        headers = {
            "Content-Disposition": f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(name)}",
            "Content-Security-Policy": FILE_CSP,
        }
        if isinstance(data, bytes):
            return Response(data, media_type=media_type, headers=headers)

        def chunks() -> Iterator[bytes]:  # the zip's temporary file, then closed
            with data:
                yield from iter(lambda: data.read(64 * 1024), b"")

        return StreamingResponse(chunks(), media_type=media_type, headers=headers)

    def requested_docs(store: RecordStore, ids: Any, year: Any) -> list[Any]:  # noqa: ANN401
        if year:
            ids = downloads.year_document_ids(store, int(year))
        elif isinstance(ids, str):
            ids = [i for i in ids.split(",") if i]
        if not isinstance(ids, list) or not ids:
            raise ValueError("no documents selected")
        return downloads.documents(store, [str(i) for i in ids])

    @app.get("/documents/{doc_id}/file")
    def document_file(request: Request, doc_id: str) -> Response:
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                doc, _ = store.get("document", doc_id)
            except (RecordNotFound, ValueError):
                return api_error("no such document", 404)
            data, name = store.blobs.get(doc.sha256), downloads.Namer(store).name(doc)
        log.info("1 document downloaded by %s", request.state.actor)
        return attachment(data, name, doc.mime)

    # The viewer (viewer.py): pictures of the pages, never the file itself; never cached on
    # disk, where they'd be unencrypted.
    @app.get("/documents/{doc_id}/view")
    def document_view(doc_id: str) -> JSONResponse:
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                doc, _ = store.get("document", doc_id)
            except (RecordNotFound, ValueError):
                return api_error("no such document", 404)
            data = store.blobs.get(doc.sha256) if viewer.shown(doc.mime) else b""
        c = doc.classification
        return JSONResponse(
            {"name": doc.original_filename, "year": c.tax_year if c else None,
             "pages": [[round(w), round(h)] for w, h in viewer.sizes(data, doc.mime)] if data
             else []},
            headers={"Cache-Control": "no-store"},
        )  # fmt: skip

    @app.get("/documents/{doc_id}/page/{number}")
    def document_page(doc_id: str, number: int) -> Response:
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                doc, _ = store.get("document", doc_id)
            except (RecordNotFound, ValueError):
                return api_error("no such document", 404)
            data = store.blobs.get(doc.sha256) if viewer.shown(doc.mime) else b""
        png = viewer.page(data, doc.mime, number) if data else None
        if png is None:
            return api_error("no such page", 404)
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": FILE_CSP}
        return Response(png, media_type="image/png", headers=headers)

    @app.get("/documents/zip")
    def documents_zip(request: Request, ids: str = "", year: int | None = None) -> Response:
        with state.locked_store() as store:
            if store is None:
                return api_error(state.current_problem() or "no vault", 503)
            try:
                docs = requested_docs(store, ids, year)
                out = downloads.zip_file(store, docs)
            except (RecordNotFound, ValueError) as e:
                return api_error(str(e) if isinstance(e, ValueError) else "no such document", 404)
        log.info("%d document(s) downloaded by %s", len(docs), request.state.actor)
        return attachment(out, downloads.zip_name(year, state.today()), "application/zip")

    @app.post("/api/documents/edit")
    async def api_document_edit(request: Request) -> JSONResponse:
        """Change a file's type and year, who sent it, its account, whose it is; detach it from /
        attach it to rows and claims. One change, undone at once."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            from taxbinder import check

            doc = str(body.get("id", ""))
            years: set[int] = set()
            name = store.get("document", doc)[0].original_filename
            with store.operation(actor, f"edit document {name}"):  # one change: one Undo
                if "doc_type" in body or "tax_year" in body:
                    raw = str(body.get("tax_year") or "").strip()
                    if raw and not raw.isdigit():
                        raise ValueError("give a tax year like 2025")
                    review.edit_document(
                        store,
                        doc,
                        actor,
                        str(body.get("doc_type") or "") or None,
                        int(raw) if raw else None,
                    )
                    if raw:
                        years.add(int(raw))
                if "issuer" in body or "account" in body:  # who sent it, the account (as Check)
                    check.edit(store, doc, actor,
                               str(body["issuer"]) if "issuer" in body else None,
                               str(body["account"]) if "account" in body else None)  # fmt: skip
                if "taxpayers" in body:  # whose it is
                    whose.set_whose(store, doc, [str(t) for t in body["taxpayers"] if t], actor)
                for target in body.get("detach") or []:
                    review.unlink_document(store, doc, str(target), actor)
                    years.add(int(str(target)[:4]))
                if target := str(body.get("attach") or ""):
                    review.link_document(store, doc, target, actor)
                    years.add(int(target[:4]))
                    matrix.read_pdfs(store, actor, only_docs={doc})  # its amounts, Across the years
            for y in sorted(years & set(years_in_vault(store))):
                refresh(store, y, actor)
            return {"id": doc}

        return act(work)

    @app.post("/api/documents/whose")
    async def api_document_whose(request: Request) -> JSONResponse:
        """Say whose a document is: people or a trust the vault knows, or the new trust named
        on it."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            doc = str(body.get("id", ""))
            chosen = [str(t) for t in body.get("taxpayers") or [] if t]
            whose.set_whose(store, doc, chosen, actor, new_trust=bool(body.get("new_trust")))
            return {"id": doc}

        return act(work)

    @app.post("/api/documents/move")
    async def api_document_move(request: Request) -> JSONResponse:
        """Move a document to the vault of the filer it's in the name of (never automatic)."""
        from taxbinder.entities import move_document

        actor = request.state.actor
        body = await request.json()
        to = str(body.get("to", ""))
        filers = {r.slug for r in list_entities(state.vault_path)}
        if to == state.current_entity() or to not in filers:
            return api_error(f"no other filer {to!r}", 404)

        def work(store: RecordStore) -> dict:
            target = state.store_for(to)
            if target is None:
                raise ValueError("that filer's vault doesn't open")
            doc = str(body.get("id", ""))
            move_document(store, target, doc, actor)
            return {"id": doc, "to": to}

        return act(work)

    @app.post("/api/returns/edit")
    async def api_return_edit(request: Request) -> JSONResponse:
        """Say whether a return is a draft or the filed copy."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            doc = str(body.get("id", ""))
            returns.set_details(store, doc, actor, status=str(body.get("status") or "") or None)
            return {"id": doc}

        return act(work)

    @app.post("/api/household/confirm")
    async def api_household_confirm(request: Request) -> JSONResponse:
        """Save the household as the person confirmed or changed it."""
        actor = request.state.actor
        body = await request.json()

        def person(p: dict) -> home.Person:
            role = p.get("role") or None
            kind = "trust" if p.get("kind") == "trust" else "individual"
            last4 = str(p.get("last4") or "") or None
            if role not in (None, *home.ROLES) or (last4 and not re.fullmatch(r"\d{4}", last4)):
                raise ValueError("a role is taxpayer, spouse or dependent; last 4 is four digits")
            return home.Person(str(p.get("name", "")), role, kind, last4, p.get("id") or None)

        def work(store: RecordStore) -> dict:
            house = home.Household(
                [person(p) for p in body.get("people", []) if str(p.get("name", "")).strip()],
                body.get("filing_status") or None,
                (str(body.get("state") or "").upper() or None),
                return_year=int(body["return_year"]) if body.get("return_year") else None,
            )
            home.confirm(store, house, tax_year(body.get("year")), actor)
            return {"people": len(house.people)}

        return act(work)

    @app.post("/api/returns/add-listed")
    async def api_return_add_listed(request: Request) -> JSONResponse:
        """Checklist rows for what a 1040 names, for its year or the next."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            year = int(body.get("year") or 0)
            return {"added": returns.add_listed(store, str(body.get("id", "")), year, actor)}

        return act(work)

    @app.post("/api/doctypes/merge")
    async def api_doctype_merge(request: Request) -> JSONResponse:
        """Move an added type's documents to another type, e.g. the built-in Tax return."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            moved = doctypes.merge_doc_type(
                store, str(body.get("from", "")), str(body.get("to", "")), actor
            )
            return {"moved": moved}

        return act(work)

    @app.post("/api/documents/delete")
    async def api_document_delete(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            ids = [str(x) for x in body.get("ids") or [body.get("id", "")]]
            years: set[int] = set()
            what = f"delete {len(ids)} documents" if len(ids) > 1 else "delete a document"
            with store.operation(actor, what):  # several at once: one Undo brings them back
                for doc in ids:
                    years |= review.delete_document(store, doc, actor)
            for y in sorted(years):
                refresh(store, y, actor)
            return {"deleted": ids if "ids" in body else body.get("id")}

        return act(work)

    # -- for my preparer (preparer.py) ----------------------------------------------

    @app.post("/api/preparer")
    async def api_preparer_add(request: Request, year: int, note: str = "") -> JSONResponse:
        """A paper for the preparer, as it is (raw body, name in X-Filename): stored, not read."""
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            return api_error("file is larger than 50 MB", 413)
        data = await request.body()
        if not data or len(data) > MAX_UPLOAD:
            return api_error("empty file" if not data else "file is larger than 50 MB", 413)
        ext = UPLOAD_TYPES.get(sniff_mime(data))
        if ext is None:
            return api_error("only PDF, PNG, JPEG, HEIC or saved HTML pages can be added", 415)
        name = safe_upload_name(request.headers.get("x-filename"), ext)
        actor = request.state.actor
        return act(lambda store: {"id": preparer.add(store, name, data, year, note, actor)})

    @app.post("/api/preparer/edit")
    async def api_preparer_edit(request: Request) -> JSONResponse:
        """`what`: "mark" (a file in the binder, for the preparer), "note", or "unmark"."""
        actor = request.state.actor
        body = await request.json()
        doc, what, note = str(body.get("id", "")), body.get("what"), body.get("note")

        def work(store: RecordStore) -> dict:
            if what == "mark":
                preparer.mark(store, doc, tax_year(int(body.get("year") or 0) or None), note, actor)
            elif what == "note":
                preparer.set_note(store, doc, note, actor)
            elif what == "unmark":
                preparer.unmark(store, doc, actor)
            else:
                raise ValueError(f"unknown change {what!r}")
            return {"id": doc}

        return act(work)

    @app.post("/api/documents/save")
    async def api_documents_save(request: Request) -> JSONResponse:
        """The app window: save into this Mac's Downloads folder (its webview has no downloads)."""
        body = await request.json()
        actor = request.state.actor

        def work(store: RecordStore) -> dict:
            year = body.get("year")
            docs = requested_docs(store, body.get("ids"), year)
            path = downloads.save(
                store,
                docs,
                state.downloads_dir,
                downloads.zip_name(year, state.today()),
                as_zip=bool(year),  # a whole year is always a zip
            )
            log.info("%d document(s) saved to Downloads by %s", len(docs), actor)
            return {"saved": str(path), "name": path.name, "count": len(docs)}

        return act(work)

    # -- backups (backup.py) -----------------------------------------------------------------

    @app.get("/backup", response_class=HTMLResponse)
    def backup_page(request: Request) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        info = backup.last(state.vault_path)
        age = when = None
        if info:
            at = datetime.fromisoformat(info["at"])
            age = (datetime.now(UTC) - at).days
            when = at.astimezone().date().isoformat()  # this Mac's date, as in the file's name
        return page(
            request,
            "backup.html",
            last=info,
            when=when,
            age=age,
            folder=state.downloads_dir,
            waiting=safe.pending(state.vault_path) is not None,
            done=safe.done(state.vault_path),
        )

    # -- settings (settings.py) ------------------------------------------------------

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, year: int | None = None) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        from importlib.metadata import version

        from taxbinder.extract.figures import LABELS

        y = pick_year(year)
        with state.locked_store() as _:
            household = state.store
            assert household is not None
            house = home.worked_out(household, y)
            chosen = settings.load(household)
            configs = []
            for each in sorted({*years_in_vault(household), y}, reverse=True):
                try:
                    configs.append(household.get("year", str(each))[0])
                except RecordNotFound:
                    configs.append(YearConfig(year=each))
            usual = {t: settings.usual_box(household, t) for t in LABELS}
        info = backup.last(state.vault_path)
        return page(
            request,
            "settings.html",
            year=y,
            house=house,
            house_data={
                "people": [asdict(p) for p in house.people],
                "filing_status": house.filing_status,
                "state": house.state,
            },
            filing_statuses=returns.FILING_STATUSES,
            state_names=returns.STATES,
            configs=configs,
            chosen=chosen,
            other_filers_on=settings.other_filers_on(household, state.vault_path),
            ticking=settings.ticking_on(household),
            usual=usual,
            box_labels=LABELS,
            type_labels=DOC_LABELS,
            backup_info=info,
            app_version=version("taxbinder"),
            inbox=state.vault_path / "inbox",
            reading=reading_status(),
        )

    # -- the reading model (llm.py): per computer, outside the binder ----------

    def reading_status() -> dict[str, Any]:
        from taxbinder import llm

        where = state.models_dir
        models, choice, in_use = llm.catalogue(where), llm.Choice.load(where), llm.current(where)
        download = state.model_download
        return {
            "memory": round(llm.memory_gb()),
            "memory_ok": llm.enough_memory(),
            "use": choice.use,
            "in_use": in_use.id if in_use else None,
            "suggested": (s.id if (s := llm.suggested(models)) else None),
            "models": [{"id": m.id, "name": m.name, "maker": m.maker, "licence": m.licence,
                        "page": m.page, "size_gb": round(m.size / 1e9, 1),
                        "here": llm.installed(where, m)} for m in models],  # fmt: skip
            "download": download.progress if download and not download.finished else None,
            "waiting": state.model_reader.waiting if state.model_reader and in_use else 0,
            "last_error": download.error if download and download.finished else "",
            "can_check": bool(llm.PUBLIC_KEY),
        }

    def reading_model(model_id: object) -> Any:  # noqa: ANN401 - an llm.Model
        from taxbinder import llm

        found = next((m for m in llm.catalogue(state.models_dir) if m.id == model_id), None)
        if found is None:
            raise ValueError("no such AI model")
        return found

    @app.get("/api/reading")
    def api_reading() -> JSONResponse:
        return JSONResponse(reading_status())

    @app.post("/api/reading/download")
    async def api_reading_download(request: Request) -> JSONResponse:
        """Download a model's files, asked for on Settings (its size shown there)."""
        from taxbinder import llm

        body = await request.json()
        if state.model_download is not None and not state.model_download.finished:
            return api_error("an AI model is downloading already", 409)
        try:
            model = reading_model(body.get("model"))
        except ValueError as e:
            return api_error(str(e), 400)
        if not llm.enough_memory():
            return api_error("this computer has too little memory for the AI model", 400)
        state.model_download = llm.Download(model, state.models_dir)
        state.model_download.start()
        choice = llm.Choice.load(state.models_dir)
        llm.Choice(model.id, choice.use).save(state.models_dir)  # in use once it's here
        return JSONResponse(state.model_download.progress)

    @app.post("/api/reading/cancel")
    def api_reading_cancel() -> JSONResponse:
        if state.model_download is not None:
            state.model_download.cancel()  # what came stays, to go on from later
        return JSONResponse({"stopped": True})

    @app.post("/api/reading/use")
    async def api_reading_use(request: Request) -> JSONResponse:
        """Reading with the model on or off; with `model`, that one (it must be here)."""
        from taxbinder import llm

        body = await request.json()
        choice = llm.Choice.load(state.models_dir)
        if "model" in body:
            try:
                model = reading_model(body["model"])
            except ValueError as e:
                return api_error(str(e), 400)
            if not llm.installed(state.models_dir, model):
                return api_error("download it first", 400)
            choice.model = model.id
        if "on" in body:
            choice.use = bool(body["on"])
        choice.save(state.models_dir)
        if state.model_reader is not None:
            state.model_reader.wake()
        return JSONResponse(reading_status())

    @app.post("/api/reading/delete")
    async def api_reading_delete(request: Request) -> JSONResponse:
        from taxbinder import llm

        body = await request.json()
        if state.model_download is not None and not state.model_download.finished:
            return api_error("wait until the download stops", 409)
        try:
            llm.delete(state.models_dir, reading_model(body.get("model")))
        except ValueError as e:
            return api_error(str(e), 400)
        return JSONResponse(reading_status())

    @app.post("/api/reading/check")
    def api_reading_check() -> JSONResponse:
        """4Ampersand's latest model list (signed); the page then offers what's newer."""
        from taxbinder import llm

        try:
            llm.check(state.models_dir)
        except (OSError, ValueError) as e:
            return api_error(str(e) if isinstance(e, ValueError) else "no connection", 400)
        return JSONResponse(reading_status())

    @app.post("/api/settings")
    async def api_settings(request: Request) -> JSONResponse:
        """Change one setting (Settings page); one undoable change in the household's vault."""
        actor = request.state.actor
        body = await request.json()
        key, value = str(body.get("key", "")), str(body.get("value", ""))

        def work(store: RecordStore) -> dict:
            settings.change(store, actor, key, value)
            if key == "other_filers" and value == "off":
                state.selected.clear()  # everyone back to the household's own vault
            return {"key": key, "value": value}

        return act(work, household=True)

    @app.post("/api/settings/reveal-inbox")
    def api_reveal_inbox() -> JSONResponse:
        state.open_file(state.vault_path / "inbox")  # Finder, on this Mac
        return JSONResponse({"opened": True})

    @app.post("/api/years/set")
    async def api_year_set(request: Request) -> JSONResponse:
        """A year's filing status and states: a move is two states and its date."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            year = tax_year(body.get("year"))
            codes = [str(s).upper() for s in body.get("states", []) if str(s).strip()]
            if any(c not in returns.STATES for c in codes) or len(codes) > 2:
                raise ValueError("choose one state, or two for a move")
            moved = str(body.get("moved_on") or "") or None
            if moved and len(codes) != 2:
                raise ValueError("a move needs the state before and the state after")
            if len(codes) == 2 and not moved:
                raise ValueError("say the date of the move")
            try:
                config, rev = store.get("year", str(year))
            except RecordNotFound:
                config, rev = YearConfig(year=year), None
            new = config.model_copy(
                update={
                    "filing_status": body.get("filing_status") or None,
                    "resident_states": codes,
                    "moved_on": date.fromisoformat(moved) if moved else None,
                }
            )
            YearConfig.model_validate(new.model_dump())  # a filing status the app knows
            if new != config or rev is None:
                with store.operation(actor, f"year {year}: filing status and states"):
                    store.put(new, rev)
            return {"year": year}

        return act(work)

    @app.post("/api/backup")
    def api_backup(request: Request) -> JSONResponse:
        """Every vault as one encrypted file in Downloads (under the lock: nothing changes
        while it's written)."""
        with state.locked_store() as _:
            household = state.store
            if household is None:
                return api_error(state.current_problem() or "no vault", 503)
            if state.demo:
                return api_error("The demo isn't backed up: it's invented, and kept apart.", 400)
            made = backup.create(household.vault, state.downloads_dir)
        log.info("backup made by %s (%d vaults)", request.state.actor, made.vaults)
        return JSONResponse({"name": made.path.name, "size": made.size, "vaults": made.vaults})

    # -- Keep it safe (safe.py): the recovery key shown once, then confirmed ------------

    @app.post("/api/safe/key")
    async def api_safe_key(request: Request) -> JSONResponse:
        """The recovery key waiting to be saved, or (`mac`) this Mac's key; never logged. Only
        on a click, never in the page itself."""
        body = await request.json()
        p = state.vault_path
        if state.current_store() is None:
            return api_error(state.current_problem() or "no vault", 503)
        if body.get("mac"):
            try:
                return JSONResponse({"key": safe.this_macs_key(p), "mac": True})
            except Exception:  # noqa: BLE001 - no key here, or the wrong one
                return api_error("This Mac doesn't have the key to your binder.", 400)
        key = safe.pending(p)
        if key is None:
            return api_error("Your recovery key was saved already; it isn't kept here.", 400)
        return JSONResponse({"key": key})

    @app.post("/api/safe/confirm")
    async def api_safe_confirm(request: Request) -> JSONResponse:
        """The key's last 4 characters typed back: saved, and removed from the Keychain."""
        body = await request.json()
        if not safe.confirm(state.vault_path, str(body.get("last4", ""))):
            return api_error("Those aren't its last 4 characters. Look again at the key.", 400)
        log.info("recovery key saved by %s", request.state.actor)
        return JSONResponse({"saved": True, "done": safe.done(state.vault_path)})

    @app.post("/api/safe/later")
    def api_safe_later() -> JSONResponse:
        """Remind me later: the app doesn't open on Keep it safe for a week (still unticked)."""
        safe.remind_later(state.vault_path, state.today())
        return JSONResponse({"go": "/"})

    # -- Across the years: key figures across years (matrix.py) -----------------------------

    def year_glance(store: RecordStore, year: int) -> dict[str, Any]:
        """One year's three stages, Gather, Check and Hand off, from the same logic as
        Check and Files for your preparer: for the panel beside each year's card."""
        from taxbinder.package import handoff

        status = year_status(store, year, state.today())
        rows = views.checklist_rows(store, year)
        return {"s": status, "a": results.answers(store, year, state.today(), rows, status),
                "made": handoff.last(store, year),
                "extended": views.extension_filed(store, year)}  # fmt: skip

    @app.post("/api/notice")
    async def api_notice(request: Request) -> JSONResponse:
        """A note answered with one click: undoable."""
        from taxbinder.notices import answer

        body = await request.json()
        actor = request.state.actor

        def work(store: RecordStore) -> dict:
            answer(store, str(body.get("id", "")), str(body.get("action", "")), actor)
            return {}

        return act(work)

    @app.get("/matrix.xlsx")
    def matrix_xlsx() -> Response:
        with state.locked_store() as store:
            if store is None:
                return api_error("no vault", 503)
            data = matrix.to_xlsx(store)
        name = f"TaxBinder Across the years {state.today():%Y-%m-%d}.xlsx"
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/matrix/save")
    def api_matrix_save(request: Request) -> JSONResponse:
        """The app window: save the Excel file into this Mac's Downloads folder."""

        def work(store: RecordStore) -> dict:
            state.downloads_dir.mkdir(parents=True, exist_ok=True)
            path = downloads.unique_path(
                state.downloads_dir, f"TaxBinder Across the years {state.today():%Y-%m-%d}.xlsx"
            )
            path.write_bytes(matrix.to_xlsx(store))
            return {"name": path.name}

        return act(work)

    def _matrix_body(body: dict) -> tuple[str, str]:
        return str(body.get("source", "")), str(body.get("doc_type", ""))

    @app.post("/api/matrix/set")
    async def api_matrix_set(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            source, doc_type = _matrix_body(body)
            try:
                year = int(body.get("year"))
            except (TypeError, ValueError):
                raise ValueError("give a year like 2024") from None
            box = body.get("box")
            box = int(box) if str(box).isdigit() else None  # a sub-row's cell: that box alone
            cell = matrix.set_cell(
                store, source, doc_type, year, str(body.get("value") or ""), actor, box
            )
            row = next(
                r for r in matrix.rows(store) if (r.source.id, r.doc_type) == (source, doc_type)
            )
            return {
                "text": cell.text,
                "parts": cell.parts,
                "part": cell.part(box or 0),
                "raw": cell.raw if box is None else cell.raw_at(box),
                "sort": str(cell.first or ""),
                "tip": row.tip(year) if year in row.cells else "",
            }

        return act(work)

    @app.post("/api/matrix/add")
    async def api_matrix_add(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            source, doc_type = _matrix_body(body)
            matrix.add_row(store, source, doc_type, str(body.get("boxes") or ""), actor)
            return {"added": f"{source}|{doc_type}"}

        return act(work)

    @app.post("/api/matrix/remove")
    async def api_matrix_remove(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            matrix.remove_row(store, *_matrix_body(body), actor)
            return {"removed": True}

        return act(work)

    @app.post("/api/matrix/boxes")
    async def api_matrix_boxes(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            source, doc_type = _matrix_body(body)
            matrix.set_boxes(store, source, doc_type, str(body.get("boxes") or ""), actor)
            return {"boxes": str(body.get("boxes") or "")}

        return act(work)

    @app.post("/api/matrix/add-all")
    def api_matrix_add_all(request: Request) -> JSONResponse:
        actor = request.state.actor
        return act(lambda store: {"added": matrix.add_all(store, actor)})

    @app.post("/api/matrix/key-boxes")
    def api_matrix_key_boxes(request: Request) -> JSONResponse:
        """Rows made before their form had key boxes (W-2s: tax withheld) follow them too."""
        actor = request.state.actor
        return act(lambda store: {"changed": matrix.add_key_boxes(store, actor)})

    def _cell_reply(store: RecordStore, source: str, doc_type: str, year: int) -> dict:
        """What the page needs to redraw one cell."""
        row = next(r for r in matrix.rows(store) if (r.source.id, r.doc_type) == (source, doc_type))
        cell = row.cells[year]
        return {
            "text": cell.text,
            "parts": cell.parts,
            "raw": cell.raw,
            "sort": str(cell.first or ""),
            "tip": row.tip(year),
        }

    @app.post("/api/matrix/accept")
    async def api_matrix_accept(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            source, doc_type = _matrix_body(body)
            year = int(body.get("year") or 0)
            matrix.accept(store, source, doc_type, year, actor)
            return _cell_reply(store, source, doc_type, year)

        return act(work)

    @app.post("/api/matrix/dismiss")
    async def api_matrix_dismiss(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            source, doc_type = _matrix_body(body)
            year = int(body.get("year") or 0)
            matrix.dismiss(store, source, doc_type, year, actor)
            return {"dismissed": True, **_cell_reply(store, source, doc_type, year)}

        return act(work)

    # -- redaction for LLM chats (redact/, privacy.py) --------------------------------------

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy_page(request: Request) -> HTMLResponse:
        if (blocked := need_store(request)) is not None:
            return blocked
        with state.locked_store() as store:
            shown = [(privacy.masked(v), v) for v in privacy.values(store)]
        return page(request, "privacy.html", values=shown, kinds=privacy.KINDS)

    @app.post("/api/privacy")
    async def api_privacy_add(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            count = privacy.add_value(
                store,
                str(body.get("kind", "")),
                str(body.get("value", "")),
                str(body.get("note", "")),
                actor,
            )
            return {"count": count}

        return act(work)

    @app.post("/api/privacy/remove")
    async def api_privacy_remove(request: Request) -> JSONResponse:
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            privacy.remove_value(store, int(body.get("index", -1)), actor)
            return {"removed": True}

        return act(work)

    def _bundle(store: RecordStore, ids: list[str], actor: str) -> Any:  # noqa: ANN401
        from taxbinder.redact import bundle

        if not ids or len(ids) > 50:
            raise ValueError("pick 1 to 50 documents")
        return bundle(store, [str(i) for i in ids], actor)

    @app.post("/api/redact")
    async def api_redact(request: Request) -> JSONResponse:
        """Selected documents as one redacted Markdown text, with what was hidden or left out."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            b = _bundle(store, body.get("ids") or [], actor)
            names = {d.id: d.original_filename for d in store.list("document")}
            log.info("prepared %d document(s) for a chat (%s)", b.documents, actor)
            return {
                "markdown": b.markdown if b.documents else "",
                "documents": b.documents,
                "hidden": dict(sorted(b.counts.items())),
                "refused": [{"name": names.get(d, d), "why": why} for d, why in b.refused],
                "scanned": [{"name": names.get(d, d), "pages": p} for d, p in b.scanned],
            }

        return act(work)

    @app.post("/api/redact/save")
    async def api_redact_save(request: Request) -> JSONResponse:
        """Save the redacted text into Downloads (the app window has no downloads)."""
        actor = request.state.actor
        body = await request.json()

        def work(store: RecordStore) -> dict:
            b = _bundle(store, body.get("ids") or [], actor)
            if not b.documents:
                raise ValueError("nothing could be redacted safely")
            state.downloads_dir.mkdir(parents=True, exist_ok=True)
            name = f"TaxBinder for chat {state.today():%Y-%m-%d}.md"
            path = downloads.unique_path(state.downloads_dir, name)
            path.write_text(b.markdown)
            return {"name": path.name}

        return act(work)

    @app.post("/api/unredact")
    async def api_unredact(request: Request) -> JSONResponse:
        from taxbinder.redact import unredact

        body = await request.json()
        text = str(body.get("text", ""))[:200_000]
        with state.locked_store() as store:
            return JSONResponse({"text": unredact(text, store)})

    binder_views.mount(app, state, page, need_store, pick_year,
                       year_starts, year_glance)  # fmt: skip
    return app
