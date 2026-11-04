"""One institution per issuer: the name read from a form finds the institution it
belongs to, however it's spelled, or becomes a new one."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from taxbinder.models import Institution
from taxbinder.slack import close
from taxbinder.slugs import institution_slug

if TYPE_CHECKING:
    from taxbinder.store import RecordStore

# Words that don't tell issuers apart: "Larkmoor Securities LLC" is "Larkmoor Securities, Inc.".
SUFFIXES = {
    "the", "inc", "incorporated", "llc", "l l c", "lp", "llp", "ltd", "limited", "co", "corp",
    "corporation", "company", "na", "n a", "fsb", "plc", "pc",
}  # fmt: skip
# What kind of institution sends this type of form (a guess, changeable).
KIND = {
    "w2": "employer",
    "1098": "bank",
    "1099_int": "bank",
    "5498_sa": "bank",
    "1099_composite": "brokerage",
    "1099_div": "brokerage",
    "1099_r": "brokerage",
    "5498": "brokerage",
    "k1_1065": "fund_sponsor",
    "k1_1120s": "fund_sponsor",
    "k1_1041": "other",
    "ssa_1099": "government",
    "property_tax_bill": "government",
    "vehicle_registration": "government",
    "donation_ack": "charity",
}


def name_key(name: str) -> str:
    """'The Larkmoor Securities, L.L.C.' → 'larkmoor securities'; '&' is 'and'."""
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", s)  # "l.l.c." → "llc."
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = s.split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def find(
    name: str | None, tin: str | None, institutions: list[Institution], scanned: bool = False
) -> str | None:
    """The institution a read name (and EIN) belongs to: the same EIN and the same first word
    ("Larkmoor Sec." is Larkmoor Securities), else the same name or alias, compared without
    case, punctuation or "Inc." / "LLC", unless both have an EIN and they differ (another
    issuer). The same EIN under a wholly new name stays apart: a misread EIN merges
    nothing; Noticed for you offers to join a renamed one (`same_ein`). `scanned`: a name read
    from a scan may have reading slips ("Tarnarack")."""
    key = name_key(name or "")
    if not key:
        return None
    if tin:  # the same EIN and the same first word (a misread EIN merges nothing)
        first = key.split()[0]
        for inst in institutions:
            names = [inst.name, *inst.aliases]
            if inst.tin == tin and any(name_key(n).split()[:1] == [first] for n in names):
                return inst.id
    same = (lambda a: close(a, key)) if scanned else (lambda a: a == key)
    for inst in institutions:
        if any(same(name_key(n)) for n in [inst.name, *inst.aliases]):
            if not (tin and inst.tin and inst.tin != tin):  # another EIN: another issuer
                return inst.id
    return None


def remember(
    name: str | None,
    tin: str | None,
    doc_type: str | None,
    institutions: list[Institution],
    scanned: bool = False,
) -> tuple[str | None, Institution | None]:
    """(the issuer's institution id, the record to write or None). A known institution learns a
    new spelling (as an alias) and its EIN; an unknown name becomes a new institution."""
    if not name:
        return None, None
    found = find(name, tin, institutions, scanned)
    if found is not None:
        inst = next(i for i in institutions if i.id == found)
        update: dict = {}
        if name not in (inst.name, *inst.aliases) and not scanned:  # a slip is no new spelling
            update["aliases"] = [*inst.aliases, name]
        if tin and not inst.tin:
            update["tin"] = tin
        return found, inst.model_copy(update=update) if update else None
    base = institution_slug(name_key(name)) or "issuer"
    taken = {i.id for i in institutions}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    kind = KIND.get(doc_type or "", "other")
    return slug, Institution(id=slug, name=name, kind=kind, tin=tin)  # type: ignore[arg-type]


def tin_of(value: object) -> str | None:
    """An EIN in its printed shape ('00-1234567'), or None if it isn't nine digits."""
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:2]}-{digits[2:]}" if len(digits) == 9 else None


def model_tin(read: dict, known: set[str]) -> str | None:
    """The sender's EIN as the model read it, used only if it's printed on the
    page or is an EIN the binder already knows (a scan's text can have slips the image hasn't)."""
    tin = tin_of(read.get("value"))
    return tin if tin and (read.get("printed") or tin in known) else None


def same_ein(institutions: list[Institution]) -> list[list[Institution]]:
    """Institutions with the same EIN under different names, one list per EIN: likely one
    issuer renamed, offered as a note to join, never joined by themselves."""
    by_tin: dict[str, list[Institution]] = {}
    for inst in sorted(institutions, key=lambda i: i.id):
        if inst.tin:
            by_tin.setdefault(inst.tin, []).append(inst)
    return [group for group in by_tin.values() if len(group) > 1]


def used_by(store: RecordStore, inst: Institution) -> str | None:
    """What keeps this institution: something that names it (a document, an account, another
    institution's history) or what the person told it (its portal, notes, when its forms come);
    None when nothing does."""
    if any(d.classification and d.classification.issuer == inst.id for d in store.list("document")):
        return "documents name it"
    sources = store.list("source")
    if any(s.institution == inst.id for s in sources):
        return "accounts (checklist rows) are under it"
    if any(i.issuer == inst.id for s in sources for i in s.identifiers):
        return "an account number is from it"
    if any(x.institution == inst.id for i in store.list("institution") for x in i.lineage):
        return "another institution's history names it"
    if inst.portal or inst.notes or inst.typical_release:
        return "it has a portal, notes or release date"
    return None


def remove(store: RecordStore, inst_id: str, actor: str) -> Institution:
    """Take out an institution nothing uses (a misread name left behind): one undoable change."""
    inst, rev = store.get("institution", inst_id)
    if why := used_by(store, inst):
        raise ValueError(f"{inst.name} can't be removed: {why}")
    with store.operation(actor, f"institution remove {inst_id}"):
        store.delete("institution", inst_id, rev)
    return inst
