"""Preparer package (§13.1): the year's documents in folders, plus index, open items and a memo.

The package is plain files for the CPA: documents are decrypted into it. Filenames use the
names of institutions and sources, never account numbers (any 4+ digit run other than a year
is dropped from names); the index shows account numbers only as hints (…1234).
"""

from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from taxbinder.models import Document
from taxbinder.package.pdf import text_pdf
from taxbinder.preparer import TAG
from taxbinder.report import DOC_LABELS as LABELS
from taxbinder.report import group_of, year_status
from taxbinder.rules.reconcile import reconcile
from taxbinder.store import RecordNotFound, RecordStore

FOLDERS = {
    "tax_return": "00-Returns",
    "return_paper": "00-Returns",
    "w2": "01-W2",
    "1099_composite": "02-1099",
    "1099_int": "02-1099",
    "1099_div": "02-1099",
    "1099_q": "02-1099",
    "1099_r": "02-1099",
    "1099_misc": "02-1099",
    "1099_nec": "02-1099",
    "ssa_1099": "02-1099",
    "k1_1065": "03-K1",
    "k1_state": "03-K1",
    "k1_1120s": "03-K1",
    "k1_1041": "03-K1",
    "1098": "04-1098-5498",
    "5498": "04-1098-5498",
    "5498_sa": "04-1098-5498",
    "payment_confirmation": "05-Estimated-Payments",
    "donation_ack": "06-Donations",
    "form_8283_support": "06-Donations",
    "property_tax_bill": "07-Property-Vehicle",
    "vehicle_registration": "07-Property-Vehicle",
    "foreign_bank_statement": "08-Foreign",
    "fbar_filing": "08-Foreign",
}
OTHER = "09-Other"
FOR_PREPARER = "10-For-preparer"  # papers handed over as they are, when there are any
ALL_FOLDERS = sorted({*FOLDERS.values(), OTHER})
EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}


def scrub(name: str) -> str:
    """Drop account-number-like digit runs (not years) and characters unsafe in filenames."""
    name = re.sub(
        r"\d{4,}",
        lambda m: m.group() if len(m.group()) == 4 and 1900 <= int(m.group()) <= 2099 else "",
        name,
    )
    name = re.sub(r'[\\/:*?"<>|_…]+', " ", name)
    name = re.sub(r"\s*-\s*(-\s*)+", " - ", name)  # separators left behind by removed digits
    return re.sub(r"\s+", " ", name).strip(" -") or "Document"


@dataclass
class PackageResult:
    root: Path
    files: dict[str, int] = field(default_factory=dict)  # folder → count
    open_items: int = 0
    unreadable: list[str] = field(default_factory=list)  # documents whose blob failed


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _targets(expectations: list[Any], claims: list[Any]) -> dict[str, list[Any]]:
    """Document id → the expectations and claims it evidences."""
    targets: dict[str, list[Any]] = defaultdict(list)
    for record in [*expectations, *claims]:
        for link in record.evidence:
            if link.role != "superseded":
                targets[link.doc].append(record)
    return targets


def _select(documents: list[Document], year: int, targets: dict[str, list[Any]]) -> list[Document]:
    """The year's documents for the package: its own and those linked to it, current versions."""
    superseded = {d.version.supersedes for d in documents if d.version.supersedes}
    return [
        d
        for d in documents
        if d.id not in superseded
        and ((d.classification and d.classification.tax_year == year) or d.id in targets)
    ]


def document_ids(store: RecordStore, year: int) -> list[str]:
    """The documents a package for the year takes now (to tell when one made is out of date)."""
    targets = _targets(store.list("expectation", year), store.list("claim", year))
    return sorted(d.id for d in _select(store.list("document"), year, targets))


def build_package(
    store: RecordStore, year: int, out: Path, actor: str, now: datetime | None = None
) -> PackageResult:
    if out.exists():
        raise FileExistsError(f"{out} already exists; move it away or choose another --out")
    now = now or datetime.now(UTC)
    reconcile(store, year, actor, now=now)
    status = year_status(store, year, now.date())

    institutions = {i.id: i for i in store.list("institution")}
    sources = {s.id: s for s in store.list("source")}
    custom = {d.id: d for d in store.list("doctype")}  # document types added in the app
    labels = {**LABELS, **{d.id: d.name for d in custom.values()}}
    expectations = store.list("expectation", year)
    claims = store.list("claim", year)
    documents: list[Document] = store.list("document")
    try:
        year_config, _ = store.get("year", str(year))
        summaries = {s.subject: s.value for s in year_config.summaries}
    except RecordNotFound:
        year_config, summaries = None, {}

    targets = _targets(expectations, claims)
    selected = _select(documents, year, targets)

    for folder in ALL_FOLDERS:
        (out / folder).mkdir(parents=True)
    result = PackageResult(out)
    index_rows = []
    used: set[Path] = set()
    for doc in sorted(selected, key=lambda d: d.id):
        c = doc.classification
        doc_type = c.doc_type if c else None
        folder = FOLDERS.get(doc_type) or (custom[doc_type].folder if doc_type in custom else OTHER)
        source = sources.get(c.source) if c and c.source else None
        inst = institutions.get(source.institution if source else (c.issuer if c else None) or "")
        linked = targets.get(doc.id, [])
        name = file_name(doc, doc_type, inst, source, linked, labels)
        if TAG in doc.tags:  # as it is, named by its note: what it's for
            folder = FOR_PREPARER
            ext = EXTENSIONS.get(doc.mime) or Path(doc.original_filename).suffix
            words = scrub(doc.preparer_note or Path(doc.original_filename).stem)
            name = re.sub(r"\s+([,.;])", r"\1", words) + ext  # no gap left before a comma
            (out / folder).mkdir(exist_ok=True)
        path = out / folder / name
        n = 2
        while path in used:
            path, n = out / folder / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
        used.add(path)
        try:
            path.write_bytes(store.blobs.get(doc.sha256))
        except Exception:  # noqa: BLE001 - listed in the memo; fsck explains
            result.unreadable.append(doc.id)
            continue
        result.files[folder] = result.files.get(folder, 0) + 1
        hints = "; ".join(
            f"{i.hint}{' (' + i.kind + ')' if i.kind != 'primary' else ''}"
            for i in (source.identifiers if source else [])
            if i.hint
        )
        amount = next(
            (t.amount for t in linked if getattr(t, "amount", None) is not None),
            next((summaries[t.id] for t in linked if t.id in summaries), ""),
        )
        index_rows.append(
            [
                f"{folder}/{path.name}",
                doc.id,
                doc_type or "",
                (c.tax_year if c else "") or "",
                inst.name if inst else "",
                source.name if source else "",
                hints,
                " ".join(t.id for t in linked),
                amount,
                " ".join(_status(t) for t in linked),
                doc.version.supersedes or "",
                doc.preparer_note or "",
            ]
        )
    _write_csv(
        out / "index.csv",
        [
            "file",
            "document",
            "doc_type",
            "tax_year",
            "institution",
            "source",
            "account",
            "linked_to",
            "amount",
            "status",
            "supersedes",
            "note",
        ],
        index_rows,
    )

    open_findings = [f for group in status.findings.values() for f in group]
    result.open_items = len(open_findings)
    _write_csv(
        out / "open-items.csv",
        ["group", "severity", "message", "suggested_action", "subjects", "finding"],
        [
            [
                group_of(f),
                f.severity,
                f.message,
                f.suggested_action or "",
                " ".join(f.subjects),
                f.id,
            ]
            for f in open_findings
        ],
    )
    _write_csv(
        out / "05-Estimated-Payments" / "schedule.csv",
        ["date", "jurisdiction", "kind", "amount", "confirmation linked"],
        [
            [c.date, c.jurisdiction, c.payment.kind, c.amount, "yes" if c.evidence else "no"]
            for c in sorted(
                (c for c in claims if c.category == "tax_payment"),
                key=lambda c: (c.jurisdiction, c.date),
            )
        ],
    )
    _write_csv(
        out / "06-Donations" / "donations.csv",
        ["date", "charity", "amount", "kind", "letter linked"],
        [
            [c.date, c.payee, c.amount, c.donation.type.replace("_", " "),
             "yes" if c.evidence else "no"]
            for c in sorted((c for c in claims if c.category == "donation"),
                            key=lambda c: (c.date, c.payee))
        ],
    )  # fmt: skip
    _write_csv(
        out / "08-Foreign" / "fbar-summary.csv",
        [
            "owner",
            "bank",
            "account",
            "currency",
            "max_balance",
            "interest",
            "tax_withheld",
            "rate",
            "max_usd",
        ],
        [
            [
                c.taxpayer or "",
                (
                    institutions[sources[c.foreign_account.source].institution].name
                    if c.foreign_account.source in sources
                    and sources[c.foreign_account.source].institution in institutions
                    else ""
                ),
                (
                    sources[c.foreign_account.source].name
                    if c.foreign_account.source in sources
                    else ""
                ),
                c.foreign_account.currency,
                c.foreign_account.max_balance,
                c.foreign_account.interest or "",
                c.foreign_account.withholding or "",
                c.foreign_account.rate or "",
                c.foreign_account.usd_value
                or (round(c.foreign_account.max_balance / c.foreign_account.rate)
                    if c.foreign_account.rate else ""),
            ]
            for c in claims
            if c.category == "fbar"
        ],
    )
    (out / "00-summary.pdf").write_bytes(
        text_pdf(_memo(year, now, store, status, expectations, selected, result, year_config))
    )
    return result


def file_name(doc: Document, doc_type: str | None, inst, source, linked, labels=None) -> str:  # noqa: ANN001
    label = (labels or LABELS).get(doc_type or "", "Document")
    if source is not None:
        parts = [inst.name if inst else "", source.name, label]
    elif linked and getattr(linked[0], "category", None) == "donation":
        short = {"donation_ack": "Receipt", "form_8283_support": "Non-cash gift records"}
        parts = ["Donation", linked[0].payee, short.get(doc_type or "", label)]
    elif linked and getattr(linked[0], "category", None) == "tax_payment":
        t = linked[0]
        parts = [f"{t.jurisdiction} payment {t.date}", label]
    else:
        parts = [inst.name if inst else "", Path(doc.original_filename).stem, label]
    ext = Path(doc.original_filename).suffix.lower() or EXTENSIONS.get(doc.mime, "")
    names = [scrub(p) for p in parts[:-1] if p]  # names come from records: scrub them
    return " - ".join([*names, parts[-1]]) + ext  # the label is ours ("1099", "5498-SA")


def _status(record) -> str:  # noqa: ANN001
    if hasattr(record, "acquisition"):
        return f"{record.acquisition}/{record.review}/{record.delivery}"
    return record.status


def _memo(year, now, store, status, expectations, selected, result, year_config) -> list[str]:  # noqa: ANN001
    counts = status.counts()
    lines = [
        f"Tax year {year}: document package",
        f"Prepared {now.date()} by TaxBinder. "
        "Account numbers appear only as their last four digits. These files aren't encrypted.",
        "",
        "Taxpayers",
        *(f"  {t.name} ({t.kind})" for t in store.list("taxpayer")),
        "",
        "Filing",
        f"  Deadline: {status.deadline or 'not set'}"
        + (f" ({status.days_left} days from preparation)" if status.deadline else ""),
    ]
    if year_config is not None:
        lines.append(f"  Extension filed: {'yes' if year_config.extension_filed else 'no'}")
        if year_config.resident_states:
            lines.append(f"  Resident state(s): {', '.join(year_config.resident_states)}")
    lines += [
        "",
        "Status",
        f"  Expected documents: {status.applicable} applicable, "
        f"{status.acquisition.get('vaulted', 0)} received and filed, "
        f"{status.verified} verified",
        f"  Claims: {status.claims} ({status.unreconciled} still missing evidence)",
        f"  Open items: {counts['error']} errors, {counts['warning']} warnings, "
        f"{counts['info']} info (all listed in open-items.csv)",
        "",
        "Still to come",
        *(_to_come(store, expectations) or ["  Nothing: every expected document is here."]),
        "",
        "Most important open items",
    ]
    errors = [
        f
        for group in ("missing", "conflicts")
        for f in status.findings[group]
        if f.severity == "error"
    ]
    lines += [f"  - {f.message}" for f in errors[:20]] or ["  None."]
    if len(errors) > 20:
        lines.append(f"  … and {len(errors) - 20} more errors")

    changes = [
        f"  - {d.original_filename} replaces {d.version.supersedes}"
        for d in selected
        if d.version.supersedes
    ]
    decisions = [
        f"  - {e.id}: {e.applicability} ({e.applicability_note})"
        for e in expectations
        if e.applicability != "applicable"
    ]
    lines += ["", "Corrected documents", *(changes or ["  None."])]
    lines += ["", "Documents not expected this year (please confirm)", *(decisions or ["  None."])]
    lines += ["", "Contents"]
    lines += [f"  {folder}: {result.files.get(folder, 0)} file(s)" for folder in ALL_FOLDERS]
    lines += ["  index.csv: every file with its source, account hint, links and status"]
    lines += ["  open-items.csv: everything not yet verified, resolved or waived"]
    if result.unreadable:
        lines += [
            "",
            f"Could not read {len(result.unreadable)} document(s): {', '.join(result.unreadable)}",
        ]
    return lines


def _to_come(store: RecordStore, expectations: list[Any]) -> list[str]:
    """Each expected document not here yet, by its institution, source and form."""
    sources = {s.id: s for s in store.list("source")}
    institutions = {i.id: i for i in store.list("institution")}
    labels = {**LABELS, **{d.id: d.name for d in store.list("doctype")}}
    lines = []
    for e in sorted(expectations, key=lambda e: e.id):
        if e.applicability != "applicable" or e.acquisition in ("vaulted", "superseded"):
            continue
        source = sources.get(e.source)
        inst = institutions.get(source.institution) if source else None
        names = [inst.name if inst else "", source.name if source else e.source]
        what = [*(scrub(n) for n in names if n), labels.get(e.doc_type, e.doc_type)]
        lines.append(f"  - {', '.join(what)}")
    return lines


def zip_name(year: int) -> str:
    return f"TaxBinder {year} for your preparer.zip"


def build_zip(
    store: RecordStore, year: int, target: Path, actor: str, now: datetime | None = None
) -> PackageResult:
    """The package as one .zip at `target` (not overwritten), under one top folder. It's made in
    a temporary folder, removed straight after: the documents in it are decrypted."""
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    top = Path(zip_name(year)).stem
    with tempfile.TemporaryDirectory(prefix="taxbinder-") as tmp:
        result = build_package(store, year, Path(tmp) / top, actor, now)
        partial = target.with_name(target.name + ".part")
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(result.root.rglob("*")):
                z.write(path, path.relative_to(tmp))
        partial.replace(target)
    result.root = target
    return result
