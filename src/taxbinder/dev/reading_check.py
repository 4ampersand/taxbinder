"""How well the readers read your own documents, in counts only.

For every Across the years amount the person typed and the document it came from, each box is read
three ways and compared with what was typed: the caption reader (by caption, with the scan's
cached recognition), the rules (`extract/forms.py`, scans recognised again), and the rules with
the model (`extract/crosscheck.py`, when a model is downloaded). What's typed is taken as right.

Run by you, never by tests on the real vault. It reads a temporary copy of
the vault, so nothing in the real vault can change, and it prints counts per form only: never a
value, a name, a document or a source.

    uv run python -m taxbinder.dev.reading_check [--vault DIR] [--models DIR] [--no-model]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from taxbinder import llm, matrix, ocr
from taxbinder.entities import list_entities
from taxbinder.extract import crosscheck
from taxbinder.extract.figures import FORM_OF, _by_caption, read_boxes
from taxbinder.ingest.inbox import scanned_pages
from taxbinder.store import RecordStore, keys_from_env, open_vault
from taxbinder.store.vault import REAL_VAULT

READERS = ("before", "rules", "model")


def new_tally() -> dict:
    return defaultdict(lambda: defaultdict(Counter))


def check_store(store: RecordStore, engine: object, model_id: str, tally: dict) -> None:
    """Adds this vault's counts to `tally` ({form: {what: Counter}})."""
    for r in matrix.rows(store):
        if not r.boxes or (r.doc_type not in FORM_OF and r.doc_type != "1099_composite"):
            continue
        for year, cell in r.cells.items():
            exp = matrix._expectation(store, year, r.source.id, r.doc_type)
            doc_id = matrix._document(store, exp) if exp is not None else None
            if doc_id is None:
                continue
            counts = tally[r.doc_type]
            counts["documents"]["read"] += 1
            doc, _ = store.get("document", doc_id)
            data = store.blobs.get(doc.sha256)
            cached = scanned_pages(store, doc)
            scan = ocr.read(data, doc.mime) if cached or doc.mime != "application/pdf" else None
            counts["documents"]["scanned"] += bool(scan)
            try:
                got = {"before": _by_caption(data, r.doc_type, r.boxes, cached),
                       "rules": read_boxes(data, r.doc_type, r.boxes, scan, year)}  # fmt: skip
                if engine is not None:
                    seen = crosscheck.read(engine, data, scan, year, doc.mime, model_id)
                    got["model"] = read_boxes(data, r.doc_type, r.boxes, scan, year, seen)
                    counts["documents"]["model answered"] += not any(
                        f.get("model_error") for f in seen["forms"].values()
                    )
            except Exception:  # noqa: BLE001 - counted, never shown
                counts["documents"]["couldn't be read"] += 1
                continue
            typed = dict(zip(r.boxes, cell.values, strict=False))
            for reader, values in got.items():
                c = counts[reader]
                c["boxes"] += len(r.boxes)
                c["found"] += len(values)
                c["documents with every box"] += all(b in values for b in r.boxes)
                for box, v in values.items():
                    if typed.get(box) is not None:  # what the person typed is taken as right
                        same = round(v.value) == round(typed[box])
                        c["same as typed" if same else "not as typed"] += 1
                    if reader == "model":
                        c["to check" if v.check else "confirmed"] += 1
                    elif reader == "before" and box in got["rules"]:
                        same = round(v.value) == round(got["rules"][box].value)
                        c["same as the rules" if same else "not as the rules"] += 1


ORDER = ("found", "documents with every box", "same as the rules", "not as the rules",
         "confirmed", "to check", "same as typed", "not as typed")  # fmt: skip


def report(tally: dict) -> str:
    lines = []
    total: dict[str, Counter] = defaultdict(Counter)
    for form in sorted(tally):
        c = tally[form]
        d = c["documents"]
        total["documents"].update(d)
        unread = d["couldn't be read"]
        more = f", {unread} couldn't be read" if unread else ""
        if "model answered" in d:
            more += f", the model answered on {d['model answered']}"
        lines.append(f"{form}: {d['read']} documents ({d['scanned']} scanned{more})")
        for reader in READERS:
            if reader in c:
                total[reader].update(c[reader])
                lines.append(f"  {reader:>6}: " + _line(c[reader]))
    d = total["documents"]
    lines.append(f"All forms: {d['read']} documents ({d['scanned']} scanned)")
    lines += [f"  {reader:>6}: " + _line(total[reader]) for reader in READERS if reader in total]
    return "\n".join(lines)


def _line(c: Counter) -> str:
    parts = [f"{c['found']} of {c['boxes']} boxes found"]
    parts += [f"{c[k]} {k}" for k in ORDER[1:] if c[k]]
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reading_check")
    ap.add_argument("--vault", type=Path, default=REAL_VAULT)
    ap.add_argument("--models", type=Path, help="where the models are (default: beside the vault)")
    ap.add_argument("--no-model", action="store_true", help="the rules only")
    args = ap.parse_args(argv)
    vault = args.vault.expanduser()
    if not (vault / "records").is_dir():
        print(f"No vault at {vault}", file=sys.stderr)
        return 2

    engine, model_id = None, ""
    model = None if args.no_model else llm.current(args.models or llm.folder(vault))
    if model is not None:
        from taxbinder.engine import Engine

        where = args.models or llm.folder(vault)
        mmproj = where / model.mmproj.name if model.mmproj else None
        engine, model_id = Engine(where / model.model.name, mmproj), model.id
    print(f"Model: {model_id or 'none (the rules only)'}", file=sys.stderr)

    tally = new_tally()
    keys = keys_from_env()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for ref in list_entities(vault):
                copy = Path(tmp) / ref.slug
                shutil.copytree(ref.path, copy, ignore=shutil.ignore_patterns("inbox"))
                with RecordStore(open_vault(copy, keys)) as store:
                    check_store(store, engine, model_id, tally)
    finally:
        if engine is not None:
            engine.stop()
    print(report(tally))
    return 0


if __name__ == "__main__":
    sys.exit(main())
