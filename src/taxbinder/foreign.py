"""Foreign accounts: each year's accounts abroad for the FBAR (FinCEN 114), shared by the
CLI and the app. The rule (R-FOR-01) and the preparer's summary (08-Foreign) read these claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from taxbinder.models import CLAIM_ADAPTER
from taxbinder.models.registry import Institution, Produces, Source
from taxbinder.store import RecordNotFound, RecordStore

THRESHOLD = Decimal(10000)  # FBAR: the highest balances together over $10,000 (not indexed)
STATEMENT = "foreign_bank_statement"


def _param(store: RecordStore, year: int, key: str) -> Decimal | None:
    """R-FOR-01's parameter for the year, as the check uses it; None without one."""
    from taxbinder.entities import entity_info
    from taxbinder.rules.params import load_params

    try:
        value = load_params(year, entity_info(store).form).get("R-FOR-01", key)
    except (ValueError, KeyError):  # a year without parameters
        return None
    return Decimal(str(value)) if value else None


def threshold(store: RecordStore, year: int) -> Decimal:
    """The year's FBAR line from its rule parameters, else the law's $10,000."""
    return _param(store, year, "fbar_aggregate_threshold") or THRESHOLD


def _money(v: Decimal | None, what: str, required: bool = False) -> Decimal | None:
    if v is None:
        if required:
            raise ValueError(f"give the {what}")
        return None
    if v < 0:
        raise ValueError(f"the {what} can't be negative")
    return v


def _currency(code: str) -> str:
    code = code.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("give the currency as its 3 letters, e.g. EUR or INR")
    return code


def _source(store: RecordStore, bank: str, account: str, owner: str) -> str:
    """The bank and account in the binder (made when new), as `add_row` does. Returns its id."""
    from taxbinder.slugs import institution_slug, slugify

    bank, account = bank.strip(), account.strip()
    if not bank or not account:
        raise ValueError("give the bank and the account")
    inst_id = institution_slug(bank)
    if not inst_id or not slugify(account):
        raise ValueError("the bank and the account need letters or digits")
    source_id = f"{inst_id}.{slugify(account)}"
    try:
        store.get("institution", inst_id)
    except RecordNotFound:
        store.put(Institution(id=inst_id, name=bank, kind="bank"), None)
    try:
        src, rev = store.get("source", source_id)
        if all(p.doc_type != STATEMENT for p in src.produces):
            produces = [*src.produces, Produces(doc_type=STATEMENT)]
            store.put(src.model_copy(update={"produces": produces}), rev)
    except RecordNotFound:
        store.put(Source(id=source_id, institution=inst_id, name=account, owner=owner,
                         produces=[Produces(doc_type=STATEMENT)]), None)  # fmt: skip
    return source_id


def add_account(
    store: RecordStore,
    year: int,
    bank: str,
    account: str,
    owner: str,
    currency: str,
    max_balance: Decimal | None,
    actor: str,
    interest: Decimal | None = None,
    rate: Decimal | None = None,
    source: str | None = None,
) -> str:
    """One foreign account for a year: its highest balance in its own currency, the interest it
    paid, and the year-end rate (units per dollar). `source`: an account already in the binder
    (last year's, offered again), else it's found or made from the bank and account names.
    One change. Returns the claim's id."""
    from taxbinder.years import EVERYONE, default_owner

    owner = owner or default_owner(store)
    if owner != EVERYONE:
        store.get("taxpayer", owner)  # RecordNotFound for an unknown person
    currency = _currency(currency)
    max_balance = _money(max_balance, "highest balance", required=True)
    if rate is not None and rate <= 0:
        raise ValueError("the exchange rate must be more than zero")
    with store.operation(actor, f"foreign account {year}: {bank.strip()}, {account.strip()}"):
        if source:  # last year's account, as it is
            source_id = store.get("source", source)[0].id
        else:
            source_id = _source(store, bank, account, owner)
        claim_id = f"{year}/fbar/{source_id}"
        try:
            store.get("claim", claim_id)
            raise ValueError(f"that account is already on {year}'s list")
        except RecordNotFound:
            pass
        claim = CLAIM_ADAPTER.validate_python({
            "id": claim_id, "year": year, "category": "fbar",
            "taxpayer": None if owner == EVERYONE else owner,
            "entered_by": actor, "entered_at": datetime.now(UTC), "provenance": "hand_entry",
            "foreign_account": {
                "source": source_id, "currency": currency, "max_balance": max_balance,
                "interest": _money(interest, "interest"), "rate": rate,
                "usd_value": max_balance if currency == "USD" else None,
            },
        })  # fmt: skip
        store.put(claim, None)
    return claim_id


def edit_account(store: RecordStore, claim_id: str, actor: str, **fields: Any) -> None:  # noqa: ANN401
    """Change an account's currency, highest balance, interest or rate (None clears one)."""
    claim, rev = store.get("claim", claim_id)
    if claim.category != "fbar":
        raise ValueError("only foreign accounts can be changed here")
    fa = claim.foreign_account.model_dump()
    if "currency" in fields:
        fa["currency"] = _currency(fields["currency"])
    if "max_balance" in fields:
        fa["max_balance"] = _money(fields["max_balance"], "highest balance", required=True)
    if "interest" in fields:
        fa["interest"] = _money(fields["interest"], "interest")
    if "rate" in fields:
        if fields["rate"] is not None and fields["rate"] <= 0:
            raise ValueError("the exchange rate must be more than zero")
        fa["rate"] = fields["rate"]
    fa["usd_value"] = fa["max_balance"] if fa["currency"] == "USD" else None
    updated = CLAIM_ADAPTER.validate_python({**claim.model_dump(), "foreign_account": fa})
    with store.operation(actor, f"foreign account {claim.year}: changed {claim_id}"):
        store.put(updated, rev)


@dataclass
class AccountRow:
    c: Any  # the FbarClaim
    bank: str
    account: str
    whose: str
    usd: Decimal | None  # its highest balance in dollars; None without a rate
    files: list[str]


@dataclass
class Person:
    name: str
    total: Decimal
    unknown: int  # accounts without a rate, so not in the total
    over: bool


def accounts(store: RecordStore, year: int) -> tuple[list[AccountRow], list[Person], Decimal]:
    """The year's accounts, each person's total in dollars against the FBAR line, and the line.
    A fact, not advice: whether an FBAR is due is the preparer's call."""
    sources = {s.id: s for s in store.list("source")}
    banks = {i.id: i.name for i in store.list("institution")}
    people = {t.id: t.name for t in store.list("taxpayer")}
    line = threshold(store, year)
    default_rate = _param(store, year, "treasury_rate_default")  # the check's, when it has one
    rows, totals, joint = [], {}, []
    for c in sorted((c for c in store.list("claim", year) if c.category == "fbar"),
                    key=lambda c: c.id):  # fmt: skip
        fa = c.foreign_account
        src = sources.get(fa.source)
        rate = fa.rate or default_rate
        usd = fa.usd_value or (fa.max_balance if fa.currency == "USD" else
                               (fa.max_balance / rate).quantize(Decimal("0.01"))
                               if rate else None)  # fmt: skip
        whose = people.get(c.taxpayer or "", "Joint")
        rows.append(AccountRow(c, banks.get(src.institution, "") if src else "",
                               src.name if src else fa.source, whose, usd,
                               [x.doc for x in c.evidence]))  # fmt: skip
        if c.taxpayer is None:  # joint: counted in full for each owner, as R-FOR-01 does
            joint.append(usd)
            continue
        total, unknown = totals.get(whose, (Decimal(0), 0))
        totals[whose] = (total + usd, unknown) if usd is not None else (total, unknown + 1)
    for name in list(totals) or (["Household"] if joint else []):
        total, unknown = totals.get(name, (Decimal(0), 0))
        for usd in joint:
            total, unknown = (total + usd, unknown) if usd is not None else (total, unknown + 1)
        totals[name] = (total, unknown)
    persons = [Person(name, t, u, t > line) for name, (t, u) in sorted(totals.items())]
    return rows, persons, line


def carry(store: RecordStore, year: int) -> list[AccountRow]:
    """Last year's accounts not yet on this year's list: offered again (amounts left empty)."""
    here = {c.foreign_account.source for c in store.list("claim", year) if c.category == "fbar"}
    return [r for r in accounts(store, year - 1)[0] if r.c.foreign_account.source not in here]
