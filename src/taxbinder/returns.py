"""Tax returns and the papers filed with them: recognised by their printed titles.

A return mentions the forms it reports (W-2s, 1099s, K-1s) and a K-1 names its return ("Schedule
K-1 (Form 1065)"), so a return is recognised only by its own title near the top of the first page,
never by a bare form number. What a 1040 says inside (`extract/return1040.py`) is kept with
it as suggestions: who the household is, and the payers and entities it names, compared here
with the vault's documents and checklist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from taxbinder.models import Document, YearConfig
from taxbinder.models.document import Listed, ReturnInfo
from taxbinder.models.year import TrackedReturn
from taxbinder.store import RecordNotFound, RecordStore

RETURN_TYPES = {"tax_return", "return_paper"}
PAPER_NAMES = {
    "8879": "e-file signature authorization",
    "4868": "extension",
    "7004": "extension",
    "1040-ES": "estimated-tax vouchers",
}
FILING_STATUSES = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
    "married_filing_separately": "Married filing separately",
    "head_of_household": "Head of household",
    "qualifying_surviving_spouse": "Qualifying surviving spouse",
}
TOTALS_ONLY = {  # the return only totals these: rows are added by hand, no issuer invented
    "wages": "You had W-2 wages: add your employers.",
    "interest": "You had interest: add the banks that paid it.",
    "dividends": "You had dividends: add who paid them.",
    "retirement": "You had IRA or pension payments: add who paid them (1099-R).",
    "social_security": "You had social security benefits (SSA-1099).",
    "mortgage_interest": "You had mortgage interest: add your lender (1098).",
}
READ_FORMS = {"1040", "1040-SR"}  # setup from a return works from a 1040 only
RETURN_PAGES = 80  # a return's schedules and statements follow its first pages
HEAD = 1500  # characters from the top of the first page where a title is looked for

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip

US = r"\bu\.\s?s\."  # "U.S." as printed in federal titles
# Papers first: an extension's title contains a return's ("… To File U.S. Individual Income Tax
# Return"). (form, title)
PAPERS = [
    ("8879", r"e-file signature authorization"),
    ("4868", rf"application for automatic extension of time to file {US} individual"),
    ("7004", r"application for automatic extension of time to file certain business"),
    ("1040-ES", r"estimated (income )?tax (payment )?voucher|estimated tax for individuals"),
]
# Federal returns, most specific first. (form, title)
FEDERAL = [
    ("1040-X", rf"amended {US} individual income tax return"),
    ("1040-NR", rf"{US} nonresident alien income tax return"),
    ("1040-SR", rf"{US} tax return for seniors"),
    ("1040", rf"{US} individual income tax return"),
    ("1041", rf"{US} income tax return for estates and trusts"),
    ("1065", rf"{US} return of partnership income"),
    ("1120-S", rf"{US} income tax return for an s corporation"),
    ("1120", rf"{US} corporation income tax return"),
]
STATE_TITLE = re.compile(
    r"(amended\s+)?((?:non-?|part-year\s+)?resident\s+)?(?:individual\s+)?income tax return",
    re.IGNORECASE,
)
FORM = re.compile(r"\bform\s+([A-Z]{0,3}-?\d{2,4}(?:-?[A-Z]{1,3})?)\b", re.IGNORECASE)
YEAR = [
    r"for the (?:full )?year (?:jan(?:uary|\.)? 1\s*[-–,]\s*dec(?:ember|\.)? 31|january 1),?\s+"
    r"(20\d{2})",
    r"(?:calendar|tax) year\s+(20\d{2})\b",
    r"(20\d{2})\s+estimated tax",
    r"income tax return\s+(?:[^\n\d]{0,40})?(20\d{2})\b",
]
DRAFT = re.compile(r"\bdo not file\b|\bdraft copy\b|^\s*draft\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Recognised:
    doc_type: str  # tax_return | return_paper
    info: ReturnInfo
    tax_year: int | None
    at: int  # where its title starts: a form named before it (a K-1's cover letter) wins


def _state(line: str) -> str | None:
    lowered = line.lower()
    found = [(lowered.find(n.lower()), code) for code, n in STATES.items() if n.lower() in lowered]
    # "West Virginia" holds "Virginia": the longest name at the same place wins
    return max(found, key=lambda f: (len(STATES[f[1]]), -f[0]))[1] if found else None


def _year(text: str, latest: int) -> int | None:
    for pattern in YEAR:
        if (m := re.search(pattern, text, re.IGNORECASE)) and 2000 <= int(m.group(1)) <= latest:
            return int(m.group(1))
    return None


def recognise(text: str, latest: int) -> Recognised | None:
    """A return or a return paper, by its title at the top of the document; None otherwise."""
    head = text[:HEAD]
    lowered = head.lower()
    federal = bool(re.search(rf"internal revenue service|department of the treasury|{US}", lowered))
    draft = "draft" if DRAFT.search(head) else "unsure"
    year = _year(head, latest)
    for form, title in PAPERS:
        if m := re.search(title, lowered):
            # its number from its own title's line: the page names other forms ("Form 540, line 17")
            line = head[head.rfind("\n", 0, m.start()) + 1 : (head + "\n").find("\n", m.end())]
            printed = FORM.search(line)
            state = None if federal else _state(head)
            info = ReturnInfo(
                form=printed.group(1).upper() if printed and state else form,
                jurisdiction=state or "federal",
            )
            return Recognised("return_paper", info, year, m.start())
    for form, title in FEDERAL:
        if m := re.search(title, lowered):
            info = ReturnInfo(form=form, status=draft, amended=form.endswith("-X"))
            return Recognised("tax_return", info, year, m.start())
    at = 0
    for line in head.splitlines(keepends=True)[:25]:
        start, at = at, at + len(line)
        m = STATE_TITLE.search(line)
        if m is None or re.search(rf"{US}|filed with", line.lower()):
            continue  # a federal title, or a W-2's "To Be Filed With … State … Income Tax Return"
        if (state := _state(line)) is None:
            continue
        printed = FORM.search(line) or FORM.search(head)
        form = printed.group(1).upper() if printed else f"{state} return"
        kind = (m.group(2) or "").lower()
        resident = None if not kind else not kind.startswith(("non", "part"))
        amended = bool(m.group(1)) or form.endswith("-X")
        info = ReturnInfo(
            form=form, jurisdiction=state, resident=resident, status=draft, amended=amended
        )
        return Recognised("tax_return", info, year, start + m.start())
    return None


def with_reading(info: ReturnInfo, data: bytes) -> ReturnInfo:
    """A 1040 with what it says read; any other return as it is. A scanned return isn't
    read inside yet: its lines come apart on a scan, and a wrong name is worse than none."""
    from taxbinder.extract.return1040 import read_1040
    from taxbinder.ingest.inbox import needs_ocr
    from taxbinder.ingest.pdftext import pdf_info

    if info.jurisdiction != "federal" or info.form not in READ_FORMS or data[:5] != b"%PDF-":
        return info
    text = pdf_info(data, RETURN_PAGES)[1]
    if needs_ocr("application/pdf", text):
        return info
    return info.model_copy(update={"read": read_1040(text)})


def return_details(store: RecordStore, doc: Document) -> ReturnInfo:
    """What kind of return a document is, for one a person says is a return: read from its title
    when it has one, else the vault's own return form, federal, not sure."""
    from taxbinder.entities import entity_info
    from taxbinder.ingest.pdftext import pdf_info

    pdf = doc.mime == "application/pdf"
    data = store.blobs.get(doc.sha256)
    found = recognise(pdf_info(data)[1] if pdf else "", 2100)
    info = found.info if found is not None else ReturnInfo(form=entity_info(store).form)
    return with_reading(info, data) if pdf else info


@dataclass
class ListedStatus:
    """A payer or entity the return names, against the vault for one year."""

    item: Listed
    institution: str | None  # the vault's institution for it, if known
    here: bool  # a document from it for that year
    row: str | None  # its checklist row that year


def listed_for(store: RecordStore, info: ReturnInfo, year: int) -> list[ListedStatus]:
    """What the return names, for `year`: the ones with no document yet first."""
    from taxbinder.institutions import find

    if info.read is None:
        return []
    institutions = store.list("institution")
    sources = {s.id: s for s in store.list("source")}
    rows = store.list("expectation", year)
    docs = [
        d
        for d in store.list("document")
        if d.classification
        and d.classification.tax_year == year
        and d.classification.doc_type not in RETURN_TYPES
    ]
    out = []
    for item in info.read.listed:
        inst = find(item.name, item.ein, institutions)
        mine = [e for e in rows if e.source in sources and sources[e.source].institution == inst]
        here = inst is not None and (
            any(d.classification.issuer == inst for d in docs) or any(e.evidence for e in mine)
        )
        out.append(ListedStatus(item, inst, here, mine[0].id if mine else None))
    return sorted(out, key=lambda s: s.here)


def add_listed(store: RecordStore, doc: str, year: int, actor: str) -> int:
    """A checklist row for `year` for each payer or entity the return names that has none (for
    the return's own year, only those with no document yet: the rest are here already); a new
    institution for one the vault doesn't know. One change, undoable. Returns how many."""
    from taxbinder.institutions import remember
    from taxbinder.models import Expectation, Source
    from taxbinder.models.registry import Produces
    from taxbinder.slugs import slugify
    from taxbinder.years import default_owner, expectation_id

    document = store.get("document", doc)[0]
    if document.tax_return is None or document.tax_return.read is None:
        raise ValueError("that document isn't a 1040 the app could read")
    own_year = document.classification and document.classification.tax_year == year
    todo = [
        s
        for s in listed_for(store, document.tax_return, year)
        if s.row is None and not (own_year and s.here)
    ]
    if not todo:
        return 0
    owner = default_owner(store)
    with store.operation(actor, f"return {doc}: {len(todo)} rows for {year}"):
        for s in todo:
            institutions = store.list("institution")
            inst, record = remember(s.item.name, s.item.ein, s.item.doc_type, institutions)
            if record is not None:
                try:
                    rev = store.get("institution", record.id)[1]
                except RecordNotFound:
                    rev = None
                store.put(record, rev)
            source_id = f"{inst}.{slugify(s.item.name)}"
            try:
                src, rev = store.get("source", source_id)
                if all(p.doc_type != s.item.doc_type for p in src.produces):
                    produces = [*src.produces, Produces(doc_type=s.item.doc_type)]
                    store.put(src.model_copy(update={"produces": produces}), rev)
            except RecordNotFound:
                src = Source(
                    id=source_id,
                    institution=inst,
                    name=s.item.name,
                    owner=owner,
                    produces=[Produces(doc_type=s.item.doc_type)],
                )
                store.put(src, None)
            exp = Expectation(
                id=expectation_id(year, source_id, s.item.doc_type),
                year=year,
                source=source_id,
                doc_type=s.item.doc_type,
                comment="Listed on the return",
            )
            store.put(exp, None)
    return len(todo)


@dataclass
class YearReturn:
    doc: Document
    info: ReturnInfo
    papers: list[tuple[Document, ReturnInfo]]


def returns_for_year(store: RecordStore, year: int) -> list[YearReturn]:
    """The year's returns, federal first, an amended one after its original; papers under the
    return of the same jurisdiction (or on their own when there's none)."""
    docs = [
        d
        for d in store.list("document")
        if d.tax_return
        and d.classification
        and d.classification.tax_year == year
        and d.classification.doc_type in RETURN_TYPES
    ]

    def order(d: Document) -> tuple:
        info = d.tax_return
        assert info is not None
        return (info.jurisdiction != "federal", info.jurisdiction, info.amended, d.acquired.at)

    returns = [
        YearReturn(d, d.tax_return, [])  # type: ignore[arg-type]
        for d in sorted(docs, key=order)
        if d.classification and d.classification.doc_type == "tax_return"
    ]
    for d in sorted(docs, key=order):
        if d.classification and d.classification.doc_type == "return_paper":
            info = d.tax_return
            assert info is not None
            home = next((r for r in returns if r.info.jurisdiction == info.jurisdiction), None)
            if home is None:
                home = YearReturn(d, info, [])
                returns.append(home)
            else:
                home.papers.append((d, info))
    return returns


def set_details(
    store: RecordStore,
    doc: str,
    actor: str,
    status: str | None = None,
    amended: bool | None = None,
    form: str | None = None,
) -> None:
    """A person says whether a return is a draft or the filed copy (or amended, or its form).
    A return's filed copy marks the year's return Filed too, unless it's further along."""
    document, rev = store.get("document", doc)
    if document.tax_return is None:
        raise ValueError("that document isn't a tax return")
    update: dict = {}
    if status is not None:
        if status not in ("draft", "filed", "unsure"):
            raise ValueError("a return is a draft, filed, or not sure")
        update["status"] = status
    if amended is not None:
        update["amended"] = amended
    if form:
        update["form"] = form.strip().upper()[:20]
    new = document.tax_return.model_copy(update=update)
    if new == document.tax_return:
        return
    what = ", ".join(f"{k} {v}" for k, v in update.items())
    with store.operation(actor, f"return {doc}: {what}"):
        store.put(document.model_copy(update={"tax_return": new}), rev)
        c = document.classification
        if new.status == "filed" and not new.amended and c and c.doc_type == "tax_return":
            _mark_filed(store, c.tax_year, new.jurisdiction)


def _mark_filed(store: RecordStore, year: int | None, jurisdiction: str) -> None:
    """The year's return for the jurisdiction is Filed, if it was not started or with the
    preparer; part of the caller's operation."""
    if year is None or (jurisdiction != "federal" and jurisdiction not in STATES):
        return
    config, rev = _config(store, year)
    old = next((t for t in config.returns if t.jurisdiction == jurisdiction), None)
    if old is not None and old.status not in ("not_started", "with_preparer"):
        return
    new = (old or TrackedReturn(jurisdiction=jurisdiction)).model_copy(update={"status": "filed"})
    kept = [t for t in config.returns if t.jurisdiction != jurisdiction]
    store.put(config.model_copy(update={"returns": [*kept, new]}), rev)


STAGES = {
    "not_started": "Not started",
    "with_preparer": "With my preparer",
    "filed": "Filed",
    "accepted": "Accepted",
    "not_needed": "Not needed",
}


def extended(config: YearConfig, documents: list[Document]) -> bool:
    """The federal return is extended: the year says so, or a Form 4868 for it is here."""
    return config.extension_filed or any(
        d.tax_return.form == "4868"
        for d in documents
        if d.tax_return and d.classification and d.classification.tax_year == config.year
    )


@dataclass
class Tracked:
    """One of the year's returns: what a person said about it, and what the vault shows."""

    t: TrackedReturn
    name: str  # "Federal", "California"
    form: str | None  # as printed on its document, else the binder's own (federal)
    copy: Document | None  # the year's return document for it marked Filed copy
    deadline: date | None  # federal only: a state's date isn't in TaxBinder
    paid: Decimal  # balance-due payments for it on Payments

    @property
    def settled(self) -> bool:
        """Nothing left to wait for: the refund arrived, or what was owed is paid."""
        if self.t.refund:
            return self.t.refund_arrived_on is not None
        return not self.t.owed or self.paid >= self.t.owed


def _config(store: RecordStore, year: int) -> tuple[YearConfig, str | None]:
    try:
        return store.get("year", str(year))
    except RecordNotFound:
        return YearConfig(year=year), None


def tracked(store: RecordStore, year: int) -> list[Tracked]:
    """The year's returns: federal, each resident state with an income tax, and any other with
    a return document or a word from a person; federal first."""
    from taxbinder.entities import entity_info
    from taxbinder.report import _deadline
    from taxbinder.states import table

    config, _ = _config(store, year)
    states = table()
    documents = store.list("document")
    docs = [
        d
        for d in documents
        if d.tax_return
        and d.classification
        and d.classification.tax_year == year
        and d.classification.doc_type == "tax_return"
    ]
    codes = ["federal"]
    codes += [s for s in config.resident_states if s in states and states[s].income_tax]
    codes += [d.tax_return.jurisdiction for d in docs]  # type: ignore[union-attr]
    codes += [t.jurisdiction for t in config.returns]
    said = {t.jurisdiction: t for t in config.returns}
    payments = [
        c
        for c in store.list("claim", year)
        if c.category == "tax_payment" and c.payment.kind == "balance_due" and not c.unpaid
    ]
    own = entity_info(store).form
    out = []
    for code in dict.fromkeys(codes):
        mine = [d for d in docs if d.tax_return.jurisdiction == code]  # type: ignore[union-attr]
        filed = [d for d in mine if d.tax_return.status == "filed"]  # type: ignore[union-attr]
        printed = (filed or mine)[0].tax_return.form if mine else None  # type: ignore[union-attr]
        out.append(
            Tracked(
                t=(
                    said.get(code, TrackedReturn(jurisdiction=code)).model_copy(
                        update={"extended": extended(config, documents)}
                    )
                    if code == "federal"
                    else said.get(code, TrackedReturn(jurisdiction=code))
                ),
                name="Federal" if code == "federal" else STATES.get(code, code),
                form=printed or (own if code == "federal" else None),
                copy=filed[0] if filed else None,
                deadline=(
                    _deadline(config, year, own, extended(config, documents))
                    if code == "federal"
                    else None
                ),
                paid=sum(
                    (c.amount for c in payments if c.jurisdiction.upper() == code.upper()),
                    Decimal(0),
                ),
            )
        )
    return out


def track(store: RecordStore, year: int, jurisdiction: str, actor: str, **changes: Any) -> None:
    """A person says where one of the year's returns is (any of TrackedReturn's fields); one
    undoable change. A refund and an amount owed don't go together; the federal return's
    extension is the year's too."""
    if jurisdiction != "federal" and jurisdiction not in STATES:
        raise ValueError(f"not a return TaxBinder knows: {jurisdiction}")
    if changes.get("status", "not_started") not in STAGES:
        raise ValueError("a return is not started, with my preparer, filed, accepted or not needed")
    config, rev = _config(store, year)
    old = next(
        (t for t in config.returns if t.jurisdiction == jurisdiction),
        TrackedReturn(jurisdiction=jurisdiction),
    )
    if jurisdiction == "federal":  # the year's extension is the federal return's
        old = old.model_copy(update={"extended": config.extension_filed})
    new = TrackedReturn.model_validate({**old.model_dump(), **changes})
    if new.refund and new.owed:
        raise ValueError("a return has a refund or an amount owed, not both")
    if any(m is not None and m <= 0 for m in (new.refund, new.owed)):
        raise ValueError("the amount must be more than zero")
    if new == old:
        return
    kept = [t for t in config.returns if t.jurisdiction != jurisdiction]
    update: dict[str, Any] = {"returns": [*kept, new]}
    if jurisdiction == "federal":
        update["extension_filed"] = new.extended
    what = ", ".join(f"{k} {v}" for k, v in changes.items() if getattr(old, k) != getattr(new, k))
    with store.operation(actor, f"return {year} {jurisdiction}: {what}"):
        store.put(config.model_copy(update=update), rev)
