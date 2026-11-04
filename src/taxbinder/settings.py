"""The app's settings, kept in the household's vault (`registry/settings.yaml`).

A setting nobody has set is worked out so that a vault keeps working as it did: other filers
are on when the vault already has one, ticking documents when a row has been ticked.
"""

from __future__ import annotations

from pathlib import Path

from taxbinder.models import AppSettings
from taxbinder.store import RecordNotFound, RecordStore


def load(store: RecordStore | None) -> AppSettings:
    if store is None:
        return AppSettings()
    try:
        return store.get("settings", "settings")[0]
    except RecordNotFound:
        return AppSettings()


def other_filers_on(store: RecordStore | None, base: Path | None) -> bool:
    from taxbinder.entities import list_entities

    said = load(store).other_filers
    if said is not None:
        return said
    return base is not None and len(list_entities(base)) > 1


def ticking_on(store: RecordStore | None) -> bool:
    said = load(store).tick_documents
    if said is not None or store is None:
        return bool(said)
    return any(e.review == "verified" for e in store.list("expectation"))


def usual_box(store: RecordStore | None, doc_type: str) -> str | None:
    """The box usually worth tracking for a form: the person's choice, else the built-in one."""
    from taxbinder.matrix import DEFAULT_BOX

    return load(store).usual_boxes.get(doc_type) or DEFAULT_BOX.get(doc_type)


def change(store: RecordStore, actor: str, key: str, value: str) -> None:
    """One setting changed, as one undoable change. `usual_box.<form>` moves the Year-by-year
    rows still on the old usual box, never rows the person set, and reads the new box."""
    from taxbinder.extract.figures import LABELS
    from taxbinder.matrix import _layout, read_pdfs
    from taxbinder.matrix import rows as year_rows

    current = load(store)
    try:
        rev = store.get("settings", "settings")[1]
    except RecordNotFound:
        rev = None
    layout = None
    if key.startswith("usual_box."):
        doc_type = key.split(".", 1)[1]
        if value not in LABELS.get(doc_type, {}):
            raise ValueError(f"{value!r} isn't a box of that form")
        old = usual_box(store, doc_type)
        new = current.model_copy(update={"usual_boxes": {**current.usual_boxes, doc_type: value}})
        layout, layout_rev = _layout(store)
        own = [r for r in (layout.rows if layout else []) if r.doc_type == doc_type and r.own]
        if layout is not None:
            from taxbinder.extract.figures import KEY_BOXES

            def keyed(box: str | None) -> list[str]:  # the usual box first, then the key ones
                return [b for b in [box, *KEY_BOXES.get(doc_type, [])] if b][:1] + [
                    b for b in KEY_BOXES.get(doc_type, []) if b != box]  # fmt: skip

            rows = [
                r.model_copy(update={"boxes": keyed(value) if r.boxes == keyed(old) else [value]})
                if r.doc_type == doc_type and r.boxes in ([old], keyed(old)) and not r.own
                else r
                for r in layout.rows
            ]
            layout = layout.model_copy(update={"rows": rows})
    elif key in ("other_filers", "tick_documents"):
        if value not in ("on", "off"):
            raise ValueError("say on or off")
        new = current.model_copy(update={key: value == "on"})
    elif key == "websites_open_in":
        if value not in ("app_window", "browser"):
            raise ValueError("say app_window or browser")
        new = current.model_copy(update={key: value})
    else:
        raise ValueError(f"unknown setting {key!r}")
    if new == current and rev is not None:
        return
    with store.operation(actor, f"settings: {key} = {value}"):
        store.put(new, rev)
        if layout is not None:
            store.put(layout, layout_rev)
        if key.startswith("usual_box."):
            moved = {(r.source.id, doc_type) for r in year_rows(store) if r.doc_type == doc_type}
            read_pdfs(store, actor, only_rows=moved - {(r.source, doc_type) for r in own})
