"""Entities: each one files its own return and has its own vault (§4, §11.1).

The household vault is the one at the usual place (`TAXBINDER_VAULT`, else Application Support).
Other entities (a C corporation, a trust …) live beside it in `entities/<slug>/vault`, locked
with the same keys, so one Keychain entry and one printed recovery key open them all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from taxbinder.models import EntityInfo, Taxpayer
from taxbinder.models.registry import RETURN_FORMS
from taxbinder.store import (
    KeyStore,
    RecordNotFound,
    RecordStore,
    init_linked_vault,
    open_vault,
)

HOUSEHOLD = "household"
HOUSEHOLD_INFO = EntityInfo(name="Household", form="1040")


@dataclass(frozen=True)
class EntityRef:
    slug: str
    path: Path


def entities_dir(base: Path) -> Path:
    return base.expanduser().parent / "entities"


def vault_path(base: Path, slug: str) -> Path:
    if slug == HOUSEHOLD:
        return base.expanduser()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError(f"invalid entity {slug!r}")
    return entities_dir(base) / slug / "vault"


def list_entities(base: Path) -> list[EntityRef]:
    """The household first, then the other entities that have a vault."""
    refs = [EntityRef(HOUSEHOLD, base.expanduser())]
    folder = entities_dir(base)
    if folder.is_dir():
        for d in sorted(folder.iterdir()):
            if (d / "vault" / "records" / "vault.yaml").exists():
                refs.append(EntityRef(d.name, d / "vault"))
    return refs


def entity_info(store: RecordStore | None) -> EntityInfo:
    if store is None:
        return HOUSEHOLD_INFO
    try:
        return store.get("entity", "entity")[0]
    except RecordNotFound:
        return HOUSEHOLD_INFO


def describe(info: EntityInfo) -> str:
    """'Form 1120 · C corporation'."""
    return RETURN_FORMS.get(info.form, f"Form {info.form}")


def create_entity(base: Path, keys: KeyStore, name: str, form: str, actor: str) -> str:
    """A new, empty vault for an entity; returns its slug."""
    name = name.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    if not slug:
        raise ValueError("the entity needs a name with letters or digits")
    if slug == HOUSEHOLD:
        raise ValueError("'Household' is the main vault's name; choose another")
    if form not in RETURN_FORMS:
        raise ValueError(f"unknown return form {form!r}")
    path = vault_path(base, slug)
    if path.exists():
        raise ValueError(f"an entity called {name!r} already exists")
    init_linked_vault(path, keys, base.expanduser())
    kind = {"1040": "individual", "1041": "trust"}.get(form, "entity")
    with RecordStore(open_vault(path, keys)) as store, store.operation(actor, f"entity {name}"):
        store.put(EntityInfo(name=name, form=form), None)  # type: ignore[arg-type]
        store.put(Taxpayer(id=slug, name=name, kind=kind), None)  # owns its accounts
    return slug


def rename_entity(store: RecordStore, name: str, actor: str) -> EntityInfo:
    """Give an entity (the household included) a new display name; recorded in its history."""
    name = name.strip()
    if not name:
        raise ValueError("the name can't be empty")
    try:
        current, rev = store.get("entity", "entity")
    except RecordNotFound:  # the household until it's first renamed
        current, rev = HOUSEHOLD_INFO, None
    renamed = EntityInfo(name=name, form=current.form)  # validates the length
    if renamed == current:
        return current
    with store.operation(actor, f"entity rename {current.name} → {name}"):
        store.put(renamed, rev)
    return renamed


def filer_for(recipient_names: list[str], others: list[tuple[str, EntityInfo]]) -> str | None:
    """The other filer whose name a document's trust is in (its vault's slug), if one is."""
    from taxbinder.institutions import name_key

    keys = {name_key(n) for n in recipient_names}
    return next((slug for slug, info in others if name_key(info.name) in keys), None)


MOVED = "moved-here"  # filed for another filer first, then moved (the diagnostic info)


def move_document(source: RecordStore, target: RecordStore, doc: str, actor: str) -> None:
    """Put a document into another filer's vault and take it out of this one, as one change in
    each (Undo here brings it back here; the other vault keeps its copy)."""
    from taxbinder.institutions import remember
    from taxbinder.models.document import Classification
    from taxbinder.review import delete_document

    document, _ = source.get("document", doc)
    data = source.blobs.get(document.sha256)
    stored = target.blobs.put(data, Path(document.original_filename).suffix.lstrip(".").lower())
    c = document.classification or Classification()
    inst_record, issuer = None, None
    if c.issuer:
        try:
            inst, _ = source.get("institution", c.issuer)
            names = target.list("institution")
            issuer, inst_record = remember(inst.name, inst.tin, c.doc_type, names)
        except RecordNotFound:
            pass
    moved = document.model_copy(
        update={
            "blob": stored.path,
            "classification": c.model_copy(update={"issuer": issuer, "source": None}),
            "tags": [*(t for t in document.tags if t != MOVED), MOVED],  # counted
        }
    )
    try:
        target.get("document", doc)
        already = True
    except RecordNotFound:
        already = False
    if not already:
        with target.operation(actor, f"moved here: {document.original_filename}"):
            if inst_record is not None:
                try:
                    _, rev = target.get("institution", inst_record.id)
                except RecordNotFound:
                    rev = None
                target.put(inst_record, rev)
            target.put(moved, None)
    delete_document(source, doc, actor)
