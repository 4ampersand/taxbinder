"""Check: "Here's what we found". The documents waiting for Check (added through
Add documents, `Document.waiting`): a **question** for each the app can't be sure about, never
guessed; the rest **look right**, by institution. "Looks good, file them" (`confirm`) is one
change in the vault: a checklist row (and its account) for each document no row matches, rows
for what a return lists, the links, and the documents no longer waiting."""

from __future__ import annotations

from dataclasses import dataclass, field

from taxbinder import perf
from taxbinder.models import Document, Expectation, Source
from taxbinder.models.registry import Identifier, Produces
from taxbinder.report import DOC_LABELS
from taxbinder.returns import RETURN_TYPES, TOTALS_ONLY, add_listed, listed_for
from taxbinder.review import link_document
from taxbinder.slugs import slugify
from taxbinder.store import RecordNotFound, RecordStore
from taxbinder.years import default_owner, expectation_id

NOT_TAX = "not-tax"  # "Not a tax document": kept, never on a row
DISTINCT = "check:distinct"  # "They're different": not a copy of the other one
LOOKS_RIGHT = "check:ok"  # a scan the person looked at
SKIPPED = "check:skip"  # left for next time
READ_OK = "check:read-ok"  # what the rules read is right, though the model read otherwise
MARKS = {"not_tax": NOT_TAX, "distinct": DISTINCT, "ok": LOOKS_RIGHT, "skip": SKIPPED,
         "read_ok": READ_OK}  # fmt: skip
NO_ROW = {  # papers that back a payment or a gift, not forms a checklist row waits for
    "donation_ack", "form_8283_support", "payment_confirmation", "property_tax_bill",
    "vehicle_registration", "account_change_notice", "other", *RETURN_TYPES,
}  # fmt: skip


@dataclass
class Found:
    doc: Document
    issuer: str  # the institution's name, or "" when not read
    account: str  # "…1234", never more
    label: str  # the type, in words
    year: int | None
    question: str | None = None  # type, year, issuer, whose, copy, return, scan
    note: str = ""  # one more line: why it's asked, or what's worth knowing
    copy_of: Document | None = None
    row: str | None = None  # the checklist row it's on already
    suggest: str = ""  # who sent it, as the model read it, where the rules differ
    suggest_year: int | None = None  # the tax year, likewise
    boxes: list[dict] = field(default_factory=list)  # key boxes in doubt (bench.doubts)

    @property
    def sure(self) -> str:
        c = self.doc.classification
        level = c.confidence if c and c.confidence is not None else 0
        return "sure" if level >= 0.9 else "fairly sure" if level >= 0.7 else "not sure"


@dataclass
class Group:
    issuer: str
    items: list[Found] = field(default_factory=list)

    @property
    def summary(self) -> str:
        labels = list(dict.fromkeys(f.label for f in self.items))
        return ", ".join(labels)


@dataclass
class Report:
    asked: list[Found]
    groups: list[Group]  # looks right, by institution
    later: list[Found]  # skipped or not a tax document
    returns: list[tuple[Found, int, list, list[str]]]  # a 1040: the year, its list, its totals

    @property
    def total(self) -> int:
        return len(self.asked) + sum(len(g.items) for g in self.groups) + len(self.later)

    @property
    def years(self) -> list[int]:
        found = [f for g in self.groups for f in g.items] + self.asked
        return sorted({f.year for f in found if f.year}, reverse=True)


def waiting(store: RecordStore) -> list[Document]:
    return [d for d in store.list("document") if d.waiting]


def _ending(account: str | None) -> str:
    digits = "".join(ch for ch in account or "" if ch.isdigit())
    return digits[-4:]


def _copy_of(doc: Document, others: list[Document]) -> Document | None:
    """Another document that looks like the same form: same type, year, issuer, account ending
    and person, and neither corrects the other."""
    c = doc.classification
    if not (c and c.doc_type and c.tax_year and c.issuer) or DISTINCT in doc.tags:
        return None
    whose = sorted(doc.recipient.taxpayers) if doc.recipient else []
    for o in others:
        k = o.classification
        if o.id == doc.id or not k or DISTINCT in o.tags:
            continue
        if doc.version.supersedes == o.id or o.version.supersedes == doc.id:
            continue
        theirs = sorted(o.recipient.taxpayers) if o.recipient else []
        if (k.doc_type, k.tax_year, k.issuer) == (c.doc_type, c.tax_year, c.issuer) and (
            _ending(k.account_number) == _ending(c.account_number)
            and (not whose or not theirs or whose == theirs)
        ):
            return o
    return None


def _model_saw(store: RecordStore, doc: Document) -> tuple[str, int | None]:
    """Who sent it and the tax year as the model read them, each only if it's printed
    on the document; ("", None) before the model has read it."""
    from taxbinder.modelread import reading

    parties = reading(store, doc).get("parties", {})
    sender, year = parties.get("sender", {}), parties.get("tax_year", {})
    name = str(sender.get("value") or "").strip() if sender.get("printed") else ""
    try:
        when = int(year["value"]) if year.get("printed") and year.get("value") else None
    except (TypeError, ValueError):
        when = None
    return name, when


def _differs(f: Found, name: str, year: int | None) -> str:
    from taxbinder.institutions import name_key

    out = []
    if name and f.issuer and name_key(name) != name_key(f.issuer):
        out.append(f"TaxBinder's rules read “{f.issuer}”, the AI model “{name}”")
    if year and f.year and year != f.year:
        out.append(f"the rules read the tax year {f.year}, the AI model {year}")
    return "; ".join(out)


@perf.timed("check.report")
def report(store: RecordStore) -> Report:
    from taxbinder.bench import doubts

    names = {i.id: i.name for i in store.list("institution")}
    docs = store.list("document")
    linked: dict[str, str] = {}
    for e in store.list("expectation"):
        for x in e.evidence:
            linked.setdefault(x.doc, e.id)
    asked, later, returns = [], [], []
    groups: dict[str, Group] = {}
    earlier = [d for d in docs if not d.waiting]  # a copy is of one that came before it
    for d in sorted((d for d in docs if d.waiting), key=lambda d: d.acquired.at):
        c = d.classification
        f = Found(
            d,
            names.get(c.issuer, c.issuer) if c and c.issuer else "",
            f"…{_ending(c.account_number)}" if c and _ending(c.account_number) else "",
            DOC_LABELS.get(c.doc_type, c.doc_type) if c and c.doc_type else "Not recognised",
            c.tax_year if c else None,
            row=linked.get(d.id),
        )
        earlier.append(d)
        if NOT_TAX in d.tags or SKIPPED in d.tags:
            f.note = "not a tax document" if NOT_TAX in d.tags else "skipped for now"
            later.append(f)
            continue
        is_return = bool(c and c.doc_type in RETURN_TYPES)
        saw, saw_year = _model_saw(store, d)
        if not (c and c.doc_type):
            f.question, f.note = "type", "TaxBinder couldn't tell what this is"
        elif not f.year:
            f.question, f.note = "year", "No tax year found on it"
        elif not f.issuer and not is_return:
            f.question, f.note = "issuer", "Who sent it couldn't be read"
            f.suggest = saw  # the model's reading, filled in to accept or change
        elif READ_OK not in d.tags and (why := _differs(f, saw, saw_year)):
            f.question, f.note = "read", why[0].upper() + why[1:]
            f.suggest = saw if saw and why.startswith("TaxBinder") else ""
            f.suggest_year = saw_year if saw_year != f.year else None
        elif d.recipient and d.recipient.ask:
            f.question, f.note = "whose", f"Addressed to “{d.recipient.printed}”"
        elif not is_return and (doubt := doubts(store, d)):
            f.question, f.boxes = "boxes", doubt
            n = len(doubt)
            f.note = f"{n} amount{'s' if n != 1 else ''} read two ways, or not adding up"
        elif (twin := _copy_of(d, earlier)) is not None:
            f.question, f.copy_of = "copy", twin
            f.note = f"Looks like the same {f.label} as {twin.original_filename}"
        elif c.doc_type == "tax_return" and d.tax_return and d.tax_return.status == "unsure":
            f.question, f.note = "return", "Is this the copy you filed, or a draft?"
        elif d.scanned and LOOKS_RIGHT not in d.tags:
            f.question, f.note = "scan", "Read from a scan or photo: is this right?"
        if c and c.fiscal_year_end and not f.question:
            f.note = f"for a fiscal year ending {c.fiscal_year_end:%b %Y}, filed with {f.year}"
        if is_return and d.tax_return and d.tax_return.read is not None and f.year:
            totals = [TOTALS_ONLY[k] for k in d.tax_return.read.totals_only if k in TOTALS_ONLY]
            for_year = _listed_year(store, d, f.year)
            returns.append((f, for_year, listed_for(store, d.tax_return, for_year), totals))
        if f.question:
            asked.append(f)
        else:
            groups.setdefault(f.issuer or "Your tax returns", Group(f.issuer or "Your tax returns"))
            groups[f.issuer or "Your tax returns"].items.append(f)
    for g in groups.values():
        g.items.sort(key=lambda f: (-(f.year or 0), f.account, f.label))
    return Report(asked, sorted(groups.values(), key=lambda g: g.issuer.lower()), later, returns)


def confirm_box(store: RecordStore, doc: str, box: str, value: str, actor: str) -> None:
    """The amount of one of its key boxes, as the person confirmed it: it wins over both
    readings, and the box isn't asked about again. One undoable change."""
    from taxbinder.matrix import key_boxes
    from taxbinder.years import parse_amount

    d, rev = store.get("document", doc)
    if box not in key_boxes(store, d):
        raise ValueError("that isn't one of its key boxes")
    amount = parse_amount(value.strip().replace("$", ""))
    if amount is None:
        raise ValueError("give an amount, e.g. 1,234.56")
    with store.operation(actor, f"check: box {box} of {d.original_filename}"):
        store.put(d.model_copy(update={"confirmed_boxes": {**d.confirmed_boxes, box: amount}}), rev)


def _listed_year(store: RecordStore, doc: Document, year: int) -> int:
    """The year a return's list is held against: its own when its documents are here (last
    year's K-1s came with it), else the next (what to expect this year)."""
    own = sum(s.here for s in listed_for(store, doc.tax_return, year))
    following = sum(s.here for s in listed_for(store, doc.tax_return, year + 1))
    return year if own > following else year + 1


def mark(store: RecordStore, doc: str, what: str, actor: str) -> None:
    """An answer on Check that's a mark on the document: not a tax document, they're
    different, a scan looks right, skip for now (and "undo" of any by Undo); "back" takes a
    skip or a not-a-tax-document off, so it's asked about or filed with the others."""
    if what == "back":
        document, rev = store.get("document", doc)
        tags = [t for t in document.tags if t not in (SKIPPED, NOT_TAX)]
        if tags != document.tags:
            with store.operation(actor, f"check: {document.original_filename}: brought back"):
                store.put(document.model_copy(update={"tags": tags}), rev)
        return
    if what not in MARKS:
        raise ValueError(f"unknown answer {what!r}")
    document, rev = store.get("document", doc)
    tag = MARKS[what]
    if tag in document.tags:
        return
    with store.operation(actor, f"check: {document.original_filename}: {what.replace('_', ' ')}"):
        store.put(document.model_copy(update={"tags": [*document.tags, tag]}), rev)


def confirm(store: RecordStore, actor: str) -> int:
    """Looks good, file them: one change. Refused while a question is open. Returns how many
    documents were filed."""
    found = report(store)
    if found.asked:
        n = len(found.asked)
        raise ValueError(f"answer the {n} question{'s' if n != 1 else ''} first")
    filing = [f for g in found.groups for f in g.items] + [
        f for f in found.later if NOT_TAX in f.doc.tags
    ]
    if not filing:
        return 0
    with store.operation(actor, f"check: filed {len(filing)} documents"):
        for f, year, _, _ in found.returns:  # rows for what the return lists, no document yet
            add_listed(store, f.doc.id, year, actor)
        for f in filing:
            c = f.doc.classification
            if f.row is None and NOT_TAX not in f.doc.tags and c and c.doc_type not in NO_ROW:
                _file_row(store, f, actor)
            doc, rev = store.get("document", f.doc.id)
            store.put(doc.model_copy(update={"waiting": False}), rev)
        from taxbinder import matrix

        matrix.read_pdfs(store, actor, only_docs={f.doc.id for f in filing})  # Across the years'
    return len(filing)


def _file_row(store: RecordStore, f: Found, actor: str) -> None:
    """A checklist row for a document no row matches: its institution's account (made when
    new) and the row, with the document on it."""
    c = f.doc.classification
    assert c and c.doc_type and c.tax_year and c.issuer
    ending = _ending(c.account_number)
    name = f"Account …{ending}" if ending else f.issuer
    source_id = f"{c.issuer}.{slugify(name) or slugify(c.doc_type)}"
    whose = f.doc.recipient.taxpayers if f.doc.recipient else []
    try:
        src, rev = store.get("source", source_id)
        if all(p.doc_type != c.doc_type for p in src.produces):
            produces = [*src.produces, Produces(doc_type=c.doc_type)]
            store.put(src.model_copy(update={"produces": produces}), rev)
    except RecordNotFound:
        ids = [Identifier(number=c.account_number, hint=f"…{ending}")] if ending else []
        store.put(
            Source(
                id=source_id,
                institution=c.issuer,
                name=name,
                owner=whose[0] if len(whose) == 1 else default_owner(store),
                identifiers=ids,
                produces=[Produces(doc_type=c.doc_type)],
            ),
            None,
        )
    exp_id = expectation_id(c.tax_year, source_id, c.doc_type)
    try:
        store.get("expectation", exp_id)
    except RecordNotFound:
        store.put(
            Expectation(
                id=exp_id, year=c.tax_year, source=source_id, doc_type=c.doc_type, comment=""
            ),
            None,
        )
    link_document(store, f.doc.id, exp_id, actor)


def edit(store: RecordStore, doc: str, actor: str, issuer: str | None = None,
         account: str | None = None) -> None:  # fmt: skip
    """Who sent it (a name: an institution the vault knows, however spelled, or a new one) and
    the account number (kept encrypted; shown as the last 4). The sender it had goes when nothing
    uses it any more (a misread name, corrected)."""
    from taxbinder.institutions import remember, used_by
    from taxbinder.models.document import Classification

    document, rev = store.get("document", doc)
    c = document.classification or Classification()
    update: dict = {"confirmed_by": actor}
    record = None
    if issuer is not None:
        if not issuer.strip():
            raise ValueError("give the name of who sent it")
        # with the document's EIN: a name typed for another EIN is another issuer
        found, record = remember(
            issuer.strip(), c.issuer_tin, c.doc_type, store.list("institution")
        )
        update["issuer"] = found
        if found != c.issuer:  # a correction of what was read
            update["method"] = "+".join(sorted(set((c.method or "").split("+")) - {""} | {"human"}))
    if account is not None:
        update["account_number"] = account.strip() or None
    with store.operation(actor, f"check: {document.original_filename}: sender or account"):
        if record is not None:
            try:
                inst_rev = store.get("institution", record.id)[1]
            except RecordNotFound:
                inst_rev = None
            store.put(record, inst_rev)
        store.put(document.model_copy(update={"classification": c.model_copy(update=update)}), rev)
        if c.issuer and update.get("issuer", c.issuer) != c.issuer:
            try:
                old, old_rev = store.get("institution", c.issuer)
            except RecordNotFound:
                old = None
            if old is not None and used_by(store, old) is None:
                store.delete("institution", old.id, old_rev)


def merge(store: RecordStore, old: str, into: str, actor: str) -> int:
    """Two institutions are one: the documents of `old` go to `into`, which learns its name
    (and EIN); `old` goes. Only for one with no accounts yet (a new spelling read on Check).
    Returns how many documents moved."""
    if old == into:
        raise ValueError("choose another institution")
    gone, gone_rev = store.get("institution", old)
    keep, keep_rev = store.get("institution", into)
    if any(s.institution == old for s in store.list("source")):
        raise ValueError(f"{gone.name} has checklist rows already; it can't be merged here")
    if gone.tin and keep.tin and gone.tin != keep.tin:  # one issuer is one EIN
        raise ValueError(
            f"{gone.name} (EIN …{gone.tin[-4:]}) and {keep.name} (EIN …{keep.tin[-4:]}) "
            "have different EINs, so they're different issuers"
        )
    docs = [
        d for d in store.list("document") if d.classification and d.classification.issuer == old
    ]
    aliases = [*keep.aliases, *(n for n in [gone.name, *gone.aliases] if n not in keep.aliases)]
    with store.operation(actor, f"check: {gone.name} is {keep.name}"):
        store.put(
            keep.model_copy(update={"aliases": aliases, "tin": keep.tin or gone.tin}), keep_rev
        )
        for d in docs:
            rev = store.get("document", d.id)[1]
            c = d.classification.model_copy(update={"issuer": into})
            store.put(d.model_copy(update={"classification": c}), rev)
        store.delete("institution", old, gone_rev)
    return len(docs)
