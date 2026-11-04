"""The reading bench: for one document, what the rules read, what the model read,
what is kept, and the rule book's findings about it.

What is kept is what Across the years takes (`figures.read_boxes`, with the model's reading when
there is one), so the bench and the table never disagree: a box both readers agree on is taken;
any other comes with the reason it needs a look, asked only for a key box (`matrix.key_boxes`).
Amounts and box names only, never an account number or a TIN.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from taxbinder import matrix, modelread, perf
from taxbinder.extract import figures, forms
from taxbinder.ingest.inbox import scanned_pages
from taxbinder.store import RecordNotFound

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from taxbinder.store import RecordStore

SAYS = {  # how each of the model's verdicts on a box reads (crosscheck.py)
    "confirmed": "the same as the rules",
    "disagree": "differs from the rules",
    "llm": "only the AI model found it",
    "check-failed": "fails the form's arithmetic",
    "rules-only": "the AI model didn't answer: the rules' value",
}


def label(doc_type: str, name: str, box: str) -> str:
    """A box's name: the one Across the years uses, else the words the rules look for on the form
    ("" when those are a pattern, not words)."""
    if named := figures.LABELS.get(doc_type, {}).get(box):
        return named
    words = forms.FORMS[name]["boxes"].get(box, "") if name in forms.FORMS else ""
    words = words.replace(r"\(", "(").replace(r"\)", ")")
    return "" if re.search(r"[\\?|$^\[]", words) else words[:1].upper() + words[1:]


def _form(doc_type: str, box: str) -> str:
    if doc_type == "1099_composite":
        return "1099_INT" if box in figures.COMPOSITE_INT else "1099_DIV"
    return figures.FORM_OF[doc_type]


def _line(
    doc_type: str, name: str, box: str, value: Any, **more: Any  # noqa: ANN401
) -> dict[str, Any] | None:
    amount = value if isinstance(value, Decimal) else figures._money(value)
    if amount is None:
        return None
    named = label(doc_type, name, box)
    return {"form": name, "box": box, "label": named, "value": f"{amount:,.2f}", **more}


def bench(store: RecordStore, doc_id: str) -> dict[str, Any]:
    """{"rules", "model", "kept": [{form, box, label, value, …}], "model_read", "checks"}."""
    doc, _ = store.get("document", doc_id)
    c = doc.classification
    doc_type, year = (c.doc_type, c.tax_year) if c else (None, None)
    out: dict[str, Any] = {"doc": doc.id, "rules": [], "model": [], "kept": [], "checks": [],
                           "model_read": doc.model_read is not None}  # fmt: skip
    if doc_type in figures.FORM_OF or doc_type == "1099_composite":
        names = figures._names(doc_type)
        data = store.blobs.get(doc.sha256)
        scans = scanned_pages(store, doc)
        reading = modelread.reading(store, doc)
        rules = forms.read(data if doc.mime == "application/pdf" else None, scans, year)
        model = reading.get("forms", {})
        boxes: list[str] = []
        for name in names:
            for box, b in rules.get(name, {}).get("boxes", {}).items():
                if line := _line(doc_type, name, box, b["value"], check=b.get("check", "")):
                    out["rules"].append(line)
            for box, b in model.get(name, {}).get("boxes", {}).items():
                says = SAYS.get(b["status"], b["status"])
                if not b.get("grounded", True):
                    says += "; not printed on the page"
                if line := _line(doc_type, name, box, b["value"], says=says):
                    out["model"].append(line)
            boxes += [b for b in forms.FORMS[name]["boxes"] if b not in boxes]
        kept = figures.read_boxes(data, doc_type, boxes, scans, year, reading or None)
        kept = figures.confirmed(kept, doc.confirmed_boxes, boxes)  # the person's answers
        key = matrix.key_boxes(store, doc)  # only these can need a look
        for box, r in sorted(kept.items(), key=lambda kv: kv[0] not in key):  # key ones first
            if line := _line(doc_type, _form(doc_type, box), box, r.value, why=r.check,
                             key=box in key):  # fmt: skip
                out["kept"].append(line)
    out["checks"] = checks(store, doc.id, year)
    return out


@perf.timed("bench.doubts", many=True)
def doubts(store: RecordStore, doc: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - a Document
    """Its key boxes the readers don't agree on, or whose reading fails a check, not yet
    answered: each with its name, why, and what each reader read ("" where it read nothing
    else). Asked on Check and on Add documents."""
    c = doc.classification
    if not c or not (c.doc_type in figures.FORM_OF or c.doc_type == "1099_composite"):
        return []
    key = [b for b in matrix.key_boxes(store, doc) if b not in doc.confirmed_boxes]
    if not key or (doc.mime != "application/pdf" and not doc.scanned):
        return []
    try:
        reading = modelread.reading(store, doc)
        data = store.blobs.get(doc.sha256)
        kept = figures.read_boxes(data, c.doc_type, key, scanned_pages(store, doc), c.tax_year,
                                  reading or None)  # fmt: skip
    except (FileNotFoundError, ValueError):  # its file or its scan's words can't be read: the
        return []  # scan question asks about it as a whole
    except Exception as e:  # noqa: BLE001 - one odd file never takes a page (or the app) down
        log.warning("reading a document's boxes failed (%s)", type(e).__name__)
        return []
    out = []
    for box in key:
        r = kept.get(box)
        if r is None or not r.check:
            continue
        name = _form(c.doc_type, box)
        b = reading.get("forms", {}).get(name, {}).get("boxes", {}).get(box, {})
        model = figures._money(b.get("value")) if b else None
        rules = figures._money(b["rule_value"]) if "rule_value" in b else (
            None if b.get("status") == "llm" else r.value)  # fmt: skip
        if model == rules:
            model = None  # the same amount, a check it fails: one amount to confirm
        out.append({
            "box": box, "label": label(c.doc_type, name, box), "why": r.check,
            "rules": "" if rules is None else f"{rules:,.2f}",
            "rules_raw": "" if rules is None else str(rules),
            "model": "" if model is None else f"{model:,.2f}",
            "model_raw": "" if model is None else str(model),
        })  # fmt: skip
    return out


def checks(store: RecordStore, doc_id: str, year: int | None) -> list[dict[str, str]]:
    """The year's open findings about this document, or about the rows it's linked to."""
    if year is None:
        return []
    try:
        found, _ = store.get("findings", str(year))
    except RecordNotFound:
        return []
    mine = {doc_id} | {
        e.id for e in store.list("expectation")
        if e.year == year and any(x.doc == doc_id for x in e.evidence)
    }  # fmt: skip
    return [
        {"severity": f.severity, "message": f.message, "rule": f.rule}
        for f in found.findings
        if f.status == "open" and mine & {*f.subjects, *f.evidence}
    ]
