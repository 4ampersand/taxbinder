"""`taxbinder` command-line interface (Typer, §11.5).

Every command works on the vault at `$TAXBINDER_VAULT`, or the real vault in Application Support
when it isn't set. The vault key is read from the macOS Keychain (`TAXBINDER_KEYSTORE` overrides,
for tests).
"""

from __future__ import annotations

import getpass
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError

from taxbinder.models.schema import export_schemas

TAX_YEAR = date.today().year - 1

app = typer.Typer(no_args_is_help=True, help="TaxBinder: track and reconcile tax documents.")
schema_app = typer.Typer(no_args_is_help=True, help="JSON Schema for record types.")
app.add_typer(schema_app, name="schema")
dev_app = typer.Typer(no_args_is_help=True, help="Developer tools.")
app.add_typer(dev_app, name="dev")
claim_app = typer.Typer(no_args_is_help=True, help="Record claims (donations, payments).")
app.add_typer(claim_app, name="claim")
applicability_app = typer.Typer(no_args_is_help=True, help="Applicability decisions.")
app.add_typer(applicability_app, name="applicability")
year_app = typer.Typer(no_args_is_help=True, help="Tax years.")
app.add_typer(year_app, name="year")
row_app = typer.Typer(no_args_is_help=True, help="Checklist rows (expected documents).")
app.add_typer(row_app, name="row")
package_app = typer.Typer(no_args_is_help=True, help="The files for your preparer, for one year.")
app.add_typer(package_app, name="package")

_ENTITY: dict[str, str] = {"slug": "household"}  # set by --entity / TAXBINDER_ENTITY


@app.callback()
def _choose_entity(
    entity: Annotated[
        str,
        typer.Option(
            "--entity",
            envvar="TAXBINDER_ENTITY",
            help="Which entity's vault to work on (see `taxbinder entity list`)",
        ),
    ] = "household",
) -> None:
    """TaxBinder: track and reconcile tax documents."""
    _ENTITY["slug"] = entity


def _vault_path() -> Path:
    """The chosen entity's vault (the household's unless --entity says otherwise)."""
    from taxbinder.entities import vault_path
    from taxbinder.store import default_vault_path

    return vault_path(default_vault_path(), _ENTITY["slug"])


Year = Annotated[int, typer.Option(help="Tax year", show_default="last year")]
Actor = Annotated[
    str, typer.Option(help="Recorded as the author of the change", show_default="your login name")
]
ACTOR = getpass.getuser()


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


@contextmanager
def open_store() -> Iterator:  # noqa: ANN401
    """The RecordStore for $TAXBINDER_VAULT, with friendly errors."""
    from taxbinder.store import (
        RecordNotFound,
        RecordStore,
        RevConflict,
        VaultKeyMissing,
        VaultLocked,
        WrongKey,
        keys_from_env,
        open_vault,
    )

    path = _vault_path()
    try:
        vault = open_vault(path, keys_from_env())
    except FileNotFoundError:
        raise fail(f"no vault at {path}; run `taxbinder init`") from None
    except VaultKeyMissing as e:
        raise fail(f"{e}; run `taxbinder restore-key`") from None
    except (WrongKey, RuntimeError) as e:
        raise fail(str(e)) from None
    try:
        store = RecordStore(vault)
    except VaultLocked:
        raise fail("the vault is in use (is the app running?)") from None
    except RuntimeError as e:  # UpgradeFailed
        raise fail(str(e)) from None
    try:
        yield store
    except (RecordNotFound, RevConflict, ValueError, KeyError) as e:
        detail = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        raise fail(detail) from None
    finally:
        store.close()


# -- vault ---------------------------------------------------------------------------------


RECOVERY_NOTE = """
Recovery key (shown once; print it and keep it somewhere safe, e.g. with your will):

    {key}

Everything in the vault is encrypted to this key as well as to the vault key in your Keychain.
If the Keychain entry is ever lost (new Mac, reset), `taxbinder restore-key` brings it back.
"""


def _create_vault() -> None:
    from taxbinder.store import default_vault_path, init_vault, keys_from_env

    try:
        vault, recovery = init_vault(default_vault_path(), keys_from_env())
    except FileExistsError as e:
        raise fail(str(e)) from None
    typer.echo(f"created vault at {vault.root}")
    typer.echo(RECOVERY_NOTE.format(key=recovery))


@app.command()
def init() -> None:
    """Create an empty, encrypted vault; prints the recovery key once."""
    _create_vault()


@app.command("restore-key")
def restore_key() -> None:
    """Put the vault key back into this Mac's Keychain using the printed recovery key."""
    from taxbinder.entities import list_entities
    from taxbinder.store import WrongKey, default_vault_path, keys_from_env
    from taxbinder.store import restore_key as restore

    secret = typer.prompt("Recovery key", hide_input=True)
    try:  # the household's vault, then every entity's (they share the keys)
        vault = restore(default_vault_path(), keys_from_env(), secret)
        typer.echo(f"key restored for {vault.root}")
        for ref in list_entities(default_vault_path())[1:]:
            restore(ref.path, keys_from_env(), secret)
            typer.echo(f"key restored for {ref.slug}")
    except (FileNotFoundError, WrongKey) as e:
        raise fail(str(e)) from None


@app.command()
def reindex() -> None:
    """Rebuild index.sqlite from the record files."""
    with open_store() as store:
        typer.echo(f"indexed {store.reindex()} records")


@app.command()
def fsck() -> None:
    """Check records, blob references and blob integrity. Exit code 1 on problems."""
    from taxbinder.store import fsck as check

    with open_store() as store:
        problems = check(store.vault)
    for p in problems:
        typer.echo(p)
    if problems:
        raise typer.Exit(1)
    typer.echo("vault OK")


@app.command()
def ingest(
    paths: Annotated[
        list[Path] | None, typer.Argument(exists=True, dir_okay=False, help="Files to add")
    ] = None,
    actor: Actor = ACTOR,
) -> None:
    """Add documents: the given files (copied, never moved), or everything in the inbox."""
    from taxbinder.ingest.inbox import Ingested, ingest_file, scan_inbox

    with open_store() as store:
        results: list[Ingested] = []
        if paths:
            for src in paths:
                copy, n = store.vault.inbox / src.name, 2
                while copy.exists():
                    copy, n = store.vault.inbox / f"{src.stem}-{n}{src.suffix}", n + 1
                shutil.copy2(src, copy)
                results.append(ingest_file(store, copy, "upload", actor))
        else:
            results = scan_inbox(store, actor)
    for r in results:
        if r.duplicate:
            what = "duplicate (acquisition recorded)"
        elif r.linked_to:
            what = f"linked to {r.linked_to}"
        else:
            options = ", ".join(f"{e} ({s})" for e, s in r.candidates[:3]) or "none"
            what = f"not matched; candidates: {options}"
        typer.echo(f"{r.doc_id}: {what}")
    if not results:
        typer.echo("nothing to ingest")


# -- reconcile and status ------------------------------------------------------------------


@app.command()
def reconcile(year: Year = TAX_YEAR, actor: Actor = ACTOR) -> None:
    """Run the rules for a year and store the findings."""
    from taxbinder.rules.reconcile import reconcile as run

    with open_store() as store:
        result = run(store, year, actor)
    open_ = [f for f in result.findings.findings if f.status == "open"]
    counts = {s: sum(f.severity == s for f in open_) for s in ("error", "warning", "info")}
    typer.echo(
        f"{year}: {counts['error']} errors, {counts['warning']} warnings, {counts['info']} info "
        f"open; {result.claims_updated} claims updated"
    )


@app.command()
def status(
    year: Year = TAX_YEAR,
    outstanding: Annotated[bool, typer.Option(help="List expectations not in the vault")] = False,
    all_: Annotated[bool, typer.Option("--all", help="Show every finding")] = False,
) -> None:
    """What is missing, what conflicts, what needs attention."""
    from taxbinder.report import format_status, year_status

    with open_store() as store:
        s = year_status(store, year, date.today())
    typer.echo(format_status(s, outstanding=outstanding, limit=None if all_ else 10))
    if s.checked and s.reconciled_at is None:
        typer.echo("Run `taxbinder reconcile` to check the year.")


@app.command()
def ack(
    finding: Annotated[str, typer.Argument(help="Finding id, as shown by `status`")],
    note: Annotated[str, typer.Option(help="Why this is fine")],
    year: Year = TAX_YEAR,
    actor: Actor = ACTOR,
) -> None:
    """Acknowledge a finding; it stays quiet until its message changes."""
    from taxbinder.rules.reconcile import acknowledge

    with open_store() as store:
        acknowledge(store, year, finding, note, actor)
    typer.echo(f"acknowledged {finding}")


# -- claims, links, decisions --------------------------------------------------------------


def _slug(text: str) -> str:
    from taxbinder.slugs import slugify

    return slugify(text)


def _free_claim_id(store, candidate: str) -> str:  # noqa: ANN001
    from taxbinder.store import RecordNotFound

    n, claim_id = 2, candidate
    while True:
        try:
            store.get("claim", claim_id)
        except RecordNotFound:
            return claim_id
        claim_id, n = f"{candidate}-{n}", n + 1


def _put_claim(data: dict, actor: str) -> str:
    from taxbinder.models import CLAIM_ADAPTER

    with open_store() as store:
        data["id"] = _free_claim_id(store, data["id"])
        claim = CLAIM_ADAPTER.validate_python(data)
        with store.operation(actor, f"claim add {claim.id}"):
            store.put(claim, None)
    return claim.id


def _entry(amount: str, day: str, year: int, actor: str) -> dict:
    return {
        "year": year,
        "date": date.fromisoformat(day),
        "amount": Decimal(amount),
        "entered_by": actor,
        "entered_at": datetime.now(UTC),
        "provenance": "hand_entry",
        "assertions": [{"value": Decimal(amount), "source": f"cli:{actor}", "kind": "hand_entry"}],
    }


@claim_app.command("donation")
def claim_donation(
    payee: Annotated[str, typer.Option(help="Charity or DAF")],
    amount: Annotated[str, typer.Option(help="Amount in USD")],
    on: Annotated[str, typer.Option("--date", help="YYYY-MM-DD")],
    kind: Annotated[Literal["cash", "securities", "property"], typer.Option("--type")] = "cash",
    to_daf: Annotated[bool, typer.Option("--to-daf", help="Contribution to a DAF")] = False,
    taxpayer: Annotated[str | None, typer.Option(help="Taxpayer id")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Record a donation (then `reconcile` attaches the evidence it needs)."""
    from taxbinder.donations import add_donation

    with open_store() as store:
        claim_id = add_donation(
            store, payee, Decimal(amount), date.fromisoformat(on), kind, actor, to_daf=to_daf
        )
    typer.echo(f"added {claim_id}")


@claim_app.command("payment")
def claim_payment(
    jurisdiction: Annotated[str, typer.Option(help="federal, CA, IL, …")],
    amount: Annotated[str, typer.Option(help="Amount in USD")],
    on: Annotated[str, typer.Option("--date", help="YYYY-MM-DD")],
    kind: Annotated[
        Literal[
            "estimated_q1",
            "estimated_q2",
            "estimated_q3",
            "estimated_q4",
            "extension",
            "balance_due",
        ],
        typer.Option(help="What the payment is for"),
    ],
    year: Annotated[int | None, typer.Option(help="Tax year it applies to")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Record an estimated, extension or balance-due payment."""
    from taxbinder.claims import add_payment

    with open_store() as store:
        claim_id = add_payment(
            store, jurisdiction, Decimal(amount), date.fromisoformat(on), kind, actor, year
        )
    typer.echo(f"added {claim_id}")


@app.command()
def link(
    doc: Annotated[str, typer.Argument(help="Document id (doc_…)")],
    target: Annotated[str, typer.Argument(help="Expectation or claim id")],
    role: Annotated[str, typer.Option(help="primary or supporting")] = "primary",
    actor: Actor = ACTOR,
) -> None:
    """Link a document as evidence for an expectation or a claim."""
    from taxbinder.review import link_document

    with open_store() as store:
        link_document(store, doc, target, actor, role)
    typer.echo(f"linked {doc} to {target}")


@applicability_app.command("set")
def applicability_set(
    expectation: Annotated[str, typer.Argument(help="Expectation id")],
    value: Annotated[
        Literal[
            "applicable",
            "not_applicable",
            "not_issued",
            "deferred_next_year",
            "below_threshold",
            "final_year_prior",
            "consolidated_elsewhere",
            "other",
        ],
        typer.Argument(),
    ],
    note: Annotated[str | None, typer.Option(help="Required unless applicable")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Decide whether a document should exist this year."""
    from taxbinder.review import set_applicability

    with open_store() as store:
        set_applicability(store, expectation, value, note, actor)
    typer.echo(f"{expectation}: {value}")


@app.command()
def waive(
    claim: Annotated[str, typer.Argument(help="Claim id")],
    requirement: Annotated[str, typer.Argument(help="Requirement id, e.g. written_acknowledgment")],
    reason: Annotated[str, typer.Option(help="e.g. not_required, lost, other")],
    note: Annotated[str, typer.Option(help="Explanation")],
    actor: Actor = ACTOR,
) -> None:
    """Waive an evidence requirement on a claim, with a reason and note."""
    from taxbinder.models.claim import Waiver

    with open_store() as store:
        record, rev = store.get("claim", claim)
        if not any(q.id == requirement for q in record.requirements):
            raise fail(f"{claim} has no requirement {requirement!r} (run `taxbinder reconcile`)")
        waiver = Waiver(reason=reason, note=note, by=actor, at=datetime.now(UTC))
        reqs = [
            q.model_copy(update={"waived": waiver}) if q.id == requirement else q
            for q in record.requirements
        ]
        with store.operation(actor, f"waive {claim} {requirement}"):
            store.put(record.model_copy(update={"requirements": reqs}), rev)
    typer.echo(f"waived {requirement} on {claim}")


@app.command()
def verify(
    expectations: Annotated[
        list[str], typer.Argument(help="Expectation ids, as shown by `status`")
    ],
    note: Annotated[str, typer.Option(help="What you checked, e.g. amounts match the statement")],
    actor: Actor = ACTOR,
) -> None:
    """Mark documents as checked (review = verified); clears their R-EXP-01 finding."""
    from taxbinder.review import set_verified

    with open_store() as store:
        set_verified(store, expectations, True, note, actor)
    for exp_id in expectations:
        typer.echo(f"verified {exp_id}")
    typer.echo("run `taxbinder reconcile` to refresh findings")


@app.command()
def unverify(
    expectations: Annotated[
        list[str], typer.Argument(help="Expectation ids, as shown by `status`")
    ],
    note: Annotated[str, typer.Option(help="Why, e.g. verified by mistake")],
    actor: Actor = ACTOR,
) -> None:
    """Undo `verify` (review = unreviewed)."""
    from taxbinder.review import set_verified

    with open_store() as store:
        set_verified(store, expectations, False, note, actor)
    for exp_id in expectations:
        typer.echo(f"unverified {exp_id}")
    typer.echo("run `taxbinder reconcile` to refresh findings")


@year_app.command("new")
def year_new(
    year: Annotated[int, typer.Argument(help="The year to start, e.g. 2026")],
    from_year: Annotated[
        int | None, typer.Option("--from", help="Copy this year's rows; default: the year before")
    ] = None,
    drop: Annotated[
        list[str] | None,
        typer.Option(help="A row of --from that won't recur (repeatable); default: the suggested"),
    ] = None,
    actor: Actor = ACTOR,
) -> None:
    """Start a year from another's checklist rows (statuses reset), adding only what's missing."""
    from taxbinder.years import carry_over, new_year

    from_year = from_year or year - 1
    with open_store() as store:
        rows = carry_over(store, year, from_year)
        dropped = set(drop) if drop is not None else {c.row.id for c in rows if not c.keep}
        n = new_year(store, year, from_year, actor, drop=dropped)
    for c in rows:
        if c.row.id in dropped:
            typer.echo(f"dropped {c.row.id}" + (f" ({c.why})" if c.why else ""))
    typer.echo(f"{year}: {n} rows copied from {from_year}" if n else f"{year}: nothing new to copy")


@row_app.command("add")
def row_add(
    year: Annotated[int, typer.Option(help="Tax year")],
    institution: Annotated[str, typer.Option(help="Institution name, existing or new")],
    source: Annotated[str, typer.Option(help="Account / fund / employer name, existing or new")],
    doc_type: Annotated[
        str, typer.Option(help="e.g. k1_1065, 1099_composite, w2, or one added (x-…)")
    ],
    owner: Annotated[
        str, typer.Option(help="Taxpayer id, e.g. primary; default: the taxpayer, or household")
    ] = "",
    amount: Annotated[str | None, typer.Option(help="Amount, e.g. 1234.50")] = None,
    comment: Annotated[str, typer.Option(help="A note for the row")] = "",
    actor: Actor = ACTOR,
) -> None:
    """Add a checklist row."""
    from taxbinder.years import add_row, parse_amount

    with open_store() as store:
        exp_id = add_row(
            store,
            year,
            institution,
            source,
            doc_type,  # type: ignore[arg-type]
            owner,
            actor,
            amount=parse_amount(amount),
            comment=comment,
        )
    typer.echo(f"added {exp_id}")


@row_app.command("edit")
def row_edit(
    expectation: Annotated[str, typer.Argument(help="Row (expectation) id")],
    amount: Annotated[str | None, typer.Option(help="New amount; empty to clear")] = None,
    comment: Annotated[str | None, typer.Option(help="New comment; empty to clear")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Change a row's amount or comment."""
    from taxbinder.years import edit_row, parse_amount

    changes: dict = {}
    if amount is not None:
        changes["amount"] = parse_amount(amount)
    if comment is not None:
        changes["comment"] = comment
    if not changes:
        raise fail("give --amount and/or --comment")
    with open_store() as store:
        edit_row(store, expectation, actor, **changes)
    typer.echo(f"edited {expectation}")


@row_app.command("delete")
def row_delete(
    expectation: Annotated[str, typer.Argument(help="Row (expectation) id")],
    actor: Actor = ACTOR,
) -> None:
    """Delete a checklist row (refused when files are attached)."""
    from taxbinder.years import delete_row

    with open_store() as store:
        delete_row(store, expectation, actor)
    typer.echo(f"deleted {expectation}")


@app.command()
def rematch(actor: Actor = ACTOR) -> None:
    """Match again every document linked to nothing; existing links are never moved."""
    from taxbinder.ingest.inbox import rematch as rematch_documents

    with open_store() as store:
        results = rematch_documents(store, actor)
    linked = [r for r in results if r.linked_to]
    for r in linked:
        typer.echo(f"linked {r.doc_id} to {r.linked_to}")
    for r in results:
        top = [c for c in r.candidates if r.candidates and c[1] == r.candidates[0][1]]
        if not r.linked_to and len(top) > 1:
            typer.echo(f"{r.doc_id}: {len(top)} rows fit equally: {', '.join(i for i, _ in top)}")
    typer.echo(f"{len(linked)} of {len(results)} unlinked document(s) matched")
    if linked:
        typer.echo("run `taxbinder reconcile` to refresh findings")


@app.command("redact")
def redact_cmd(
    docs: Annotated[list[str], typer.Argument(help="Document ids (see the Documents page)")],
    out: Annotated[
        Path | None, typer.Option(help="Write the Markdown here (default: print it)")
    ] = None,
    actor: Actor = ACTOR,
) -> None:
    """Documents as one redacted Markdown text, safe to paste into an LLM chat."""
    from taxbinder.redact import bundle

    with open_store() as store:
        b = bundle(store, docs, actor)
    for doc, why in b.refused:
        typer.echo(f"left out {doc}: {why}", err=True)
    for doc, pages in b.scanned:
        typer.echo(f"{doc}: pages {pages} have no text (scanned) and were left out", err=True)
    hidden = ", ".join(f"{k} {v}" for k, v in sorted(b.counts.items())) or "nothing"
    typer.echo(f"{b.documents} document(s); hidden: {hidden}", err=True)
    if not b.documents:
        raise typer.Exit(1)
    if out:
        out.write_text(b.markdown)
        typer.echo(f"written to {out}", err=True)
    else:
        typer.echo(b.markdown)


@app.command("unredact")
def unredact_cmd() -> None:
    """Put the real values back into an LLM's answer: paste it on stdin."""
    import sys

    from taxbinder.redact import unredact

    text = sys.stdin.read()
    with open_store() as store:
        typer.echo(unredact(text, store))


key_app = typer.Typer(no_args_is_help=True, help="This Mac's vault key.")
app.add_typer(key_app, name="key")


@key_app.command("show")
def key_show() -> None:
    """Print this Mac's vault key, to keep in a password manager or on paper. It opens your
    vault, its entities and every backup, like the recovery key: use it if that one is lost."""
    from taxbinder.store import (
        VaultKeyMissing,
        WrongKey,
        default_vault_path,
        keys_from_env,
        vault_key,
    )

    typer.echo(
        "This prints the key that unlocks all your tax documents. Show it only where nobody\n"
        "can see your screen, and don't paste it into chats, email or a document."
    )
    if not typer.confirm("Show it?", default=False):
        raise typer.Exit(1)
    try:
        secret = vault_key(default_vault_path(), keys_from_env())
    except (VaultKeyMissing, WrongKey, FileNotFoundError) as e:
        raise fail(str(e)) from None
    typer.echo(f"\n  {secret}\n")
    typer.echo(
        "Keep it in your password manager (e.g. a 1Password secure note) or on paper in a safe\n"
        "place. With it, on any Mac: `taxbinder restore-key` (paste it), or\n"
        "`taxbinder backup restore`."
    )


backup_app = typer.Typer(no_args_is_help=True, help="Encrypted backups of every vault.")
app.add_typer(backup_app, name="backup")


@backup_app.command("create")
def backup_create(
    out: Annotated[Path, typer.Option(help="Folder for the backup file")] = Path("~/Downloads"),
) -> None:
    """One encrypted file with the household's vault and every entity's (opens only with this
    Mac's key or the recovery key). Safe to keep in a cloud drive."""
    from taxbinder import backup

    with open_store() as store:  # holds the vault lock: no change lands halfway
        made = backup.create(store.vault, out.expanduser())
    typer.echo(
        f"saved {made.path} ({made.size / 1e6:.1f} MB, {made.vaults} vault(s), {made.files} files)"
    )


@backup_app.command("restore")
def backup_restore(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="A .tbak file")],
    to: Annotated[Path, typer.Option(help="A new, empty folder to unpack into")],
) -> None:
    """Unpack a backup into a new folder with the recovery key (never over a vault in use)."""
    from taxbinder import backup

    secret = typer.prompt("Recovery key (or this Mac's key)", hide_input=True)
    try:
        manifest = backup.restore(file, secret, to.expanduser())
    except ValueError as e:
        raise fail(str(e)) from None
    typer.echo(
        f"restored {len(manifest['vaults'])} vault(s) from {manifest['created'][:10]} into {to}"
    )
    typer.echo(
        f"look inside: TAXBINDER_VAULT={to}/vault uv run taxbinder status  (then restore-key)\n"
        "to use it instead of this Mac's vault: quit TaxBinder, move ~/Library/Application "
        "Support/com.4ampersand.taxbinder aside, and put this folder's contents there"
    )


@backup_app.command("status")
def backup_status() -> None:
    """When the last backup was made from this Mac."""
    from taxbinder import backup
    from taxbinder.store import default_vault_path

    info = backup.last(default_vault_path())
    typer.echo(f"last backup: {info['at'][:16]} ({info['name']})" if info else "no backup yet")


across_app = typer.Typer(
    no_args_is_help=True, help="Across the years: each document's key amounts, year by year."
)
app.add_typer(across_app, name="acrossyears")


@across_app.command("show")
def across_show() -> None:
    """Print Across the years (whole dollars)."""
    from taxbinder import matrix

    with open_store() as store:
        cols, data = matrix.years(store), matrix.rows(store)
    head = f"{'Type':8} {'Document':34} {'ID':7} {'Box':7}" + "".join(f"{y:>16}" for y in cols)
    typer.echo(head)
    for r in data:
        cells = "".join(f"{r.cells[y].text:>16}" for y in cols)
        typer.echo(
            f"{r.label[:8]:8} {r.source.name[:34]:34} {r.ident:7} {', '.join(r.boxes)[:7]:7}{cells}"
        )


@across_app.command("read")
def across_read(actor: Actor = ACTOR) -> None:
    """Read each row's boxes from every year's PDF into suggestions (accept them in the app)."""
    from taxbinder import matrix

    with open_store() as store:
        c = matrix.read_pdfs(store, actor)
    typer.echo(
        f"{c['read']} document(s) read: {c['suggested']} new value(s), "
        f"{c['conflicts']} different from what's entered"
    )


@across_app.command("set")
def across_set(
    source: Annotated[str, typer.Argument(help="Source id, e.g. fernhollow-properties.kettle-410")],
    doc_type: Annotated[str, typer.Argument(help="e.g. k1_1065")],
    year: Annotated[int, typer.Argument()],
    value: Annotated[str, typer.Argument(help="'4535' or, for two boxes, '4535 / 9764'")],
    actor: Actor = ACTOR,
) -> None:
    """Type one amount: a row (its source and kind of document) in one year."""
    from taxbinder import matrix

    with open_store() as store:
        cell = matrix.set_cell(store, source, doc_type, year, value, actor)
    typer.echo(f"{year}: {cell.text or '(cleared)'}")


privacy_app = typer.Typer(
    no_args_is_help=True, help="Private details to hide when redacting (stored encrypted)."
)
app.add_typer(privacy_app, name="privacy")


@privacy_app.command("add")
def privacy_add(
    kind: Annotated[str, typer.Argument(help="name, address, number, email, phone or other")],
    note: Annotated[str, typer.Option(help="Who or what it is, e.g. 'daughter'")] = "",
    actor: Actor = ACTOR,
) -> None:
    """Add a value to hide. It's asked for (not an argument), so it stays out of shell history."""
    from taxbinder.privacy import add_value

    secret = typer.prompt("Value", hide_input=True)
    with open_store() as store:
        count = add_value(store, kind, secret, note, actor)
    typer.echo(f"saved ({count} private details)")


@privacy_app.command("list")
def privacy_list() -> None:
    """The private details, masked."""
    from taxbinder.privacy import masked, values

    with open_store() as store:
        got = values(store)
    for i, v in enumerate(got, start=1):
        typer.echo(f"{i:3}  {v.kind:8} {masked(v):28} {v.note}")
    if not got:
        typer.echo("none yet: `taxbinder privacy add name --note spouse`")


@privacy_app.command("remove")
def privacy_remove(
    number: Annotated[int, typer.Argument(help="The number `privacy list` shows")],
    actor: Actor = ACTOR,
) -> None:
    from taxbinder.privacy import remove_value

    with open_store() as store:
        remove_value(store, number - 1, actor)
    typer.echo("removed")


doctype_app = typer.Typer(no_args_is_help=True, help="Document types you add.")
app.add_typer(doctype_app, name="doctype")


@doctype_app.command("add")
def doctype_add(
    name: Annotated[str, typer.Argument(help="e.g. 'Form 592-B'")],
    folder: Annotated[str, typer.Option(help="Accountant package folder, e.g. 03-K1")] = "09-Other",
    keyword: Annotated[
        list[str] | None, typer.Option(help="Words that identify it (repeatable)")
    ] = None,
    actor: Actor = ACTOR,
) -> None:
    """Add a document type (then use its id with `row add --doc-type`)."""
    from taxbinder.doctypes import add_doc_type

    with open_store() as store:
        d = add_doc_type(store, name, folder, keyword or [], actor)
    typer.echo(f"{d.id}: {d.name} → {d.folder}")


@doctype_app.command("list")
def doctype_list() -> None:
    """Built-in and added document types."""
    from taxbinder.doctypes import custom_types
    from taxbinder.models.common import BUILTIN_DOC_TYPES
    from taxbinder.report import DOC_LABELS

    with open_store() as store:
        added = custom_types(store)
    for t in BUILTIN_DOC_TYPES:
        typer.echo(f"{t:24} {DOC_LABELS.get(t, t)}")
    for d in added:
        words = f"  (words: {', '.join(d.keywords)})" if d.keywords else ""
        typer.echo(f"{d.id:24} {d.name} → {d.folder}{words}")


@doctype_app.command("merge")
def doctype_merge(
    old: Annotated[str, typer.Argument(help="An added type, e.g. x-tax-return")],
    new: Annotated[str, typer.Argument(help="The type its documents become, e.g. tax_return")],
    actor: Actor = ACTOR,
) -> None:
    """Move every document of an added type to another type (one undoable change)."""
    from taxbinder.doctypes import merge_doc_type

    with open_store() as store:
        moved = merge_doc_type(store, old, new, actor)
    typer.echo(f"{moved} document(s) moved from {old} to {new}")


institution_app = typer.Typer(no_args_is_help=True, help="Institutions: who sends documents.")
app.add_typer(institution_app, name="institution")


@institution_app.command("list")
def institution_list() -> None:
    """Every institution, with what keeps it (or that nothing does)."""
    from taxbinder.institutions import used_by

    with open_store() as store:
        for inst in sorted(store.list("institution"), key=lambda i: i.name.lower()):
            typer.echo(f"{inst.id:32} {inst.name}  ({used_by(store, inst) or 'nothing uses it'})")


@institution_app.command("remove")
def institution_remove(
    inst_id: Annotated[str, typer.Argument(help="Its id, from `institution list`")],
    actor: Actor = ACTOR,
) -> None:
    """Remove an institution nothing uses, e.g. a misread name left behind (undoable)."""
    from taxbinder.institutions import remove

    with open_store() as store:
        typer.echo(f"removed {remove(store, inst_id, actor).name}")


return_app = typer.Typer(no_args_is_help=True, help="Tax returns (draft or filed).")
app.add_typer(return_app, name="return")


@return_app.command("list")
def return_list(year: Year = TAX_YEAR) -> None:
    """The year's returns (where each is) and its return documents and papers."""
    from taxbinder.returns import STAGES, returns_for_year, tracked

    with open_store() as store:
        found = returns_for_year(store, year)
        where = tracked(store, year)
    for t in where:
        came = f"  refund {t.t.refund}" if t.t.refund else f"  owed {t.t.owed}" if t.t.owed else ""
        extended = "  extended" if t.t.extended else ""
        typer.echo(f"{t.t.jurisdiction:8} {STAGES[t.t.status]}{extended}{came}")
    if not found:
        typer.echo(f"no return documents for {year}")
    for r in found:
        i = r.info
        flags = " amended" if i.amended else ""
        typer.echo(f"{r.doc.id}  {i.jurisdiction:8} {i.form:10} {i.status}{flags}")
        for p, pi in r.papers:
            typer.echo(f"  {p.id}  {pi.form}")


@return_app.command("track")
def return_track(
    jurisdiction: Annotated[str, typer.Argument(help="federal, or a state: CA, NY, …")],
    year: Year = TAX_YEAR,
    status: Annotated[
        str | None,
        typer.Option(help="not_started | with_preparer | filed | accepted | not_needed"),
    ] = None,
    filed_on: Annotated[str | None, typer.Option(help="The day it was filed: 2026-03-02")] = None,
    extended: Annotated[bool | None, typer.Option(help="An extension was filed")] = None,
    refund: Annotated[str | None, typer.Option(help="What comes back, e.g. 1200")] = None,
    owed: Annotated[str | None, typer.Option(help="What is still to pay")] = None,
    refund_arrived_on: Annotated[str | None, typer.Option(help="The day the refund came")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Say where one of the year's returns is; only the options given change."""
    from taxbinder.returns import track
    from taxbinder.years import parse_amount

    given: dict = {}
    if status:
        given["status"] = status
    if filed_on:
        given["filed_on"] = date.fromisoformat(filed_on)
    if extended is not None:
        given["extended"] = extended
    if refund:  # a refund or an amount owed, never both
        given |= {"refund": parse_amount(refund), "owed": None}
    if owed:
        given |= {"owed": parse_amount(owed), "refund": None}
    if refund_arrived_on:
        given["refund_arrived_on"] = date.fromisoformat(refund_arrived_on)
    code = "federal" if jurisdiction.lower() == "federal" else jurisdiction.upper()
    with open_store() as store:
        track(store, year, code, actor, **given)


@return_app.command("status")
def return_status(
    doc: Annotated[str, typer.Argument(help="Document id, e.g. doc_0123456789ab")],
    status: Annotated[str, typer.Argument(help="draft | filed | unsure")],
    actor: Actor = ACTOR,
) -> None:
    """Say whether a return is a draft or the filed copy."""
    from taxbinder.returns import set_details

    with open_store() as store:
        set_details(store, doc, actor, status=status)
    typer.echo(f"{doc}: {status}")


@return_app.command("show")
def return_show(
    doc: Annotated[str, typer.Argument(help="Document id of a 1040")],
) -> None:
    """What a 1040 says: household, and what it lists against the checklist."""
    from taxbinder.returns import FILING_STATUSES, TOTALS_ONLY, listed_for

    with open_store() as store:
        document = store.get("document", doc)[0]
        info = document.tax_return
        if info is None or info.read is None:
            raise typer.BadParameter("that document isn't a 1040 the app could read")
        year = document.classification.tax_year if document.classification else None
        listed = listed_for(store, info, year) if year else []
    read = info.read
    typer.echo(f"filing status: {FILING_STATUSES.get(read.filing_status or '', 'not read')}")
    for p in read.people:
        typer.echo(f"{p.role:10} {p.name}" + (f"  …{p.last4}" if p.last4 else ""))
    typer.echo(f"state: {read.state or 'not read'} (from the address; to confirm)")
    for s in listed:
        where = "here" if s.here else "expected, no document" if s.row else "no document"
        ein = f"  …{s.item.ein[-4:]}" if s.item.ein else ""
        typer.echo(f"  {s.item.doc_type:15} {s.item.name}{ein}  [{where}]")
    for kind in read.totals_only:
        typer.echo(f"  {TOTALS_ONLY[kind]}")


@return_app.command("add-listed")
def return_add_listed(
    doc: Annotated[str, typer.Argument(help="Document id of a 1040")],
    year: Year = TAX_YEAR,
    actor: Actor = ACTOR,
) -> None:
    """Checklist rows for what the return names that has none (its own year: missing only)."""
    from taxbinder.returns import add_listed

    with open_store() as store:
        added = add_listed(store, doc, year, actor)
    typer.echo(f"added {added} rows to {year}")


settings_app = typer.Typer(no_args_is_help=True, help="The app's settings.")
app.add_typer(settings_app, name="settings")


@settings_app.command("show")
def settings_show() -> None:
    """Every setting, with what it is now (worked out when nobody has set it)."""
    from taxbinder import settings
    from taxbinder.extract.figures import LABELS

    with open_store() as store:
        base = store.vault.root
        typer.echo(f"other_filers     {'on' if settings.other_filers_on(store, base) else 'off'}")
        typer.echo(f"tick_documents   {'on' if settings.ticking_on(store) else 'off'}")
        typer.echo(f"websites_open_in {settings.load(store).websites_open_in}")
        for t in LABELS:
            typer.echo(f"usual_box.{t:15} {settings.usual_box(store, t)}")


@settings_app.command("set")
def settings_set(
    key: Annotated[
        str, typer.Argument(help="other_filers, tick_documents, websites_open_in, usual_box.<form>")
    ],  # fmt: skip
    value: Annotated[str, typer.Argument(help="on/off, app_window/browser, or a box")],
    actor: Actor = ACTOR,
) -> None:
    """Change one setting (undoable)."""
    from taxbinder import settings

    with open_store() as store:
        settings.change(store, actor, key, value)
    typer.echo(f"{key} = {value}")


household_app = typer.Typer(no_args_is_help=True, help="The household.")
app.add_typer(household_app, name="household")


@household_app.command("show")
def household_show(year: Year = TAX_YEAR) -> None:
    """The household: confirmed, or worked out from the return or the documents."""
    from taxbinder.household import worked_out
    from taxbinder.returns import FILING_STATUSES

    with open_store() as store:
        h = worked_out(store, year)
    found = {"confirmed": "confirmed", "return": f"from the {h.return_year} return",
             "documents": f"from the names on {h.documents} documents",
             "nothing": "nothing to go on yet"}[h.found_in]  # fmt: skip
    typer.echo(f"household ({found}):")
    for p in h.people:
        last4 = f"  …{p.last4}" if p.last4 else ""
        typer.echo(f"  {p.role or p.kind:10} {p.name}{last4}")
    typer.echo(f"filing status: {FILING_STATUSES.get(h.filing_status or '', 'not known')}")
    typer.echo(f"state: {h.state or 'not known'}")


@household_app.command("confirm")
def household_confirm(year: Year = TAX_YEAR, actor: Actor = ACTOR) -> None:
    """Save the household as `household show` has it (change it in the app first if needed)."""
    from taxbinder.household import confirm, worked_out

    with open_store() as store:
        h = worked_out(store, year)
        if h.confirmed:
            typer.echo("already confirmed")
            return
        confirm(store, h, year, actor)
    typer.echo(f"confirmed {len(h.people)} people")


document_app = typer.Typer(no_args_is_help=True, help="Whose a document is; moving it.")
app.add_typer(document_app, name="document")


@document_app.command("whose")
def document_whose(
    doc: Annotated[str, typer.Argument(help="Document id, e.g. doc_0123456789ab")],
    taxpayers: Annotated[list[str] | None, typer.Argument(help="Taxpayer ids (both: two)")] = None,
    new_trust: Annotated[bool, typer.Option(help="The trust named on it becomes known")] = False,
    actor: Actor = ACTOR,
) -> None:
    """Say whose a document is: one person, both, or a trust."""
    from taxbinder.whose import set_whose

    with open_store() as store:
        try:
            set_whose(store, doc, taxpayers or [], actor, new_trust=new_trust)
        except ValueError as e:
            raise fail(str(e)) from None
        typer.echo(f"{doc}: {', '.join(store.get('document', doc)[0].recipient.taxpayers)}")


@document_app.command("move")
def document_move(
    doc: Annotated[str, typer.Argument(help="Document id")],
    to: Annotated[str, typer.Option(help="The other filer's slug (see `taxbinder entity list`)")],
    actor: Actor = ACTOR,
) -> None:
    """Move a document into another filer's vault (out of this one)."""
    from taxbinder.entities import move_document, vault_path
    from taxbinder.store import RecordStore, default_vault_path, keys_from_env, open_vault

    if to == _ENTITY["slug"]:
        raise fail("that's the vault it's in")
    try:
        target_path = vault_path(default_vault_path(), to)
        target = RecordStore(open_vault(target_path, keys_from_env()))
    except (ValueError, FileNotFoundError) as e:
        raise fail(f"no filer {to!r}: {e}") from None
    with target, open_store() as store:
        move_document(store, target, doc, actor)
    typer.echo(f"{doc}: moved to {to}")


entity_app = typer.Typer(no_args_is_help=True, help="Entities that file their own return.")
app.add_typer(entity_app, name="entity")


@entity_app.command("add")
def entity_add(
    name: Annotated[str, typer.Argument(help="e.g. 'Northwind Holdings Inc.'")],
    form: Annotated[str, typer.Option(help="1120, 1120-S, 1065, 1041 or 1040")] = "1120",
    actor: Actor = ACTOR,
) -> None:
    """Add an entity: a new vault beside the household's, opened by the same keys."""
    from taxbinder.entities import create_entity
    from taxbinder.store import default_vault_path, keys_from_env

    try:
        slug = create_entity(default_vault_path(), keys_from_env(), name, form, actor)
    except ValueError as e:
        raise fail(str(e)) from None
    typer.echo(f"added {slug}: use `taxbinder --entity {slug} …` to work on it")


@entity_app.command("rename")
def entity_rename(
    name: Annotated[str, typer.Argument(help="New name, e.g. 'Quill Family'")],
    actor: Actor = ACTOR,
) -> None:
    """Rename the entity chosen with --entity (the household by default)."""
    from taxbinder.entities import rename_entity

    with open_store() as store:
        info = rename_entity(store, name, actor)
    typer.echo(f"renamed {_ENTITY['slug']} to {info.name}")


@entity_app.command("list")
def entity_list() -> None:
    """The household and every entity."""
    from taxbinder.entities import entity_info, list_entities
    from taxbinder.store import RecordStore, default_vault_path, keys_from_env, open_vault

    for ref in list_entities(default_vault_path()):
        try:
            with RecordStore(open_vault(ref.path, keys_from_env())) as store:
                info = entity_info(store)
            typer.echo(f"{ref.slug:28} {info.name} · Form {info.form}")
        except Exception as e:  # noqa: BLE001 - e.g. in use by the app
            typer.echo(f"{ref.slug:28} (can't open: {e})")


bill_app = typer.Typer(no_args_is_help=True, help="Bills and scheduled payments.")
app.add_typer(bill_app, name="bill")


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise fail(f"{text!r} isn't a date (YYYY-MM-DD)") from None


@bill_app.command("add")
def bill_add(
    payee: Annotated[str, typer.Argument(help="Who it's paid to")],
    installment: Annotated[list[str], typer.Option(help="AMOUNT@YYYY-MM-DD[:label], repeatable")],
    kind: Annotated[
        str, typer.Option(help="property_tax, vehicle_fee, tax_payment, bill")
    ] = "bill",
    jurisdiction: Annotated[str, typer.Option(help="For tax_payment: federal, CA …")] = "",
    payment_kind: Annotated[str, typer.Option(help="For tax_payment: estimated_q4 …")] = "",
    actor: Actor = ACTOR,
) -> None:
    """Add a bill: one planned payment per installment (TaxBinder never pays)."""
    from taxbinder.bills import add_bill
    from taxbinder.years import parse_amount

    parts = []
    for item in installment:
        amount, _, rest = item.partition("@")
        due, _, label = rest.partition(":")
        parts.append((label, parse_amount(amount), _day(due)))
    with open_store() as store:
        ids = add_bill(store, kind, payee, parts, actor, None, jurisdiction, payment_kind)  # type: ignore[arg-type]
    for i in ids:
        typer.echo(f"planned {i}")


@bill_app.command("list")
def bill_list() -> None:
    """Planned and scheduled payments, soonest first."""
    from taxbinder.bills import upcoming

    with open_store() as store:
        rows = upcoming(store)
    for c in rows:
        when = f"scheduled {c.plan.pay_on}" if c.plan.state == "scheduled" else "planned"
        amount = f"${c.amount:,.2f}" if c.amount is not None else ""
        typer.echo(f"due {c.plan.due}  {amount:>14}  {c.plan.payee}  ({when})  {c.id}")
    if not rows:
        typer.echo("nothing to pay")


@bill_app.command("schedule")
def bill_schedule(
    claim: Annotated[str, typer.Argument(help="Payment id, from `bill list`")],
    on: Annotated[str, typer.Option("--on", help="Date it's scheduled for")],
    note: Annotated[str, typer.Option(help="e.g. 'Chase bill pay'")] = "",
    actor: Actor = ACTOR,
) -> None:
    """Record that a payment is scheduled with your bank or the payee."""
    from taxbinder.bills import schedule

    with open_store() as store:
        schedule(store, claim, _day(on), note, actor)
    typer.echo(f"scheduled {claim} for {on}")


@bill_app.command("paid")
def bill_paid(
    claim: Annotated[str, typer.Argument(help="Payment id, from `bill list`")],
    on: Annotated[str, typer.Option("--on", help="Date it was paid")],
    amount: Annotated[str | None, typer.Option(help="If different from planned")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Mark a planned payment paid (then `link` the confirmation to it)."""
    from taxbinder.bills import mark_paid
    from taxbinder.years import parse_amount

    with open_store() as store:
        new_id = mark_paid(store, claim, _day(on), actor, parse_amount(amount or ""))
    typer.echo(f"paid {new_id}")


@app.command()
def history(
    all_: Annotated[bool, typer.Option("--all", help="Include automatic re-checks")] = False,
    limit: Annotated[int, typer.Option(help="How many")] = 30,
) -> None:
    """Recent changes to the vault, newest first (ids for `taxbinder undo`)."""
    with open_store() as store:
        changes = store.vault.history.changes(limit=limit * 4 if not all_ else limit)
    shown = [c for c in changes if all_ or not c.automatic][:limit]
    for c in shown:
        when = c.time.astimezone().strftime("%Y-%m-%d %H:%M")
        typer.echo(f"{c.sha[:12]}  {when}  {c.author:22}  {c.title}")


@app.command()
def undo(
    change: Annotated[str, typer.Argument(help="Change id from `taxbinder history` (12+ chars)")],
    actor: Actor = ACTOR,
) -> None:
    """Put back the records a change touched, as they were before it (recorded as a change)."""
    from taxbinder.rules.reconcile import reconcile

    with open_store() as store:
        match = [c for c in store.vault.history.changes(limit=500) if c.sha.startswith(change)]
        if len(match) != 1:
            raise fail(f"no single change starts with {change!r}")
        years = store.undo(match[0].sha, actor)
        for y in sorted(years):
            reconcile(store, y, actor)
    typer.echo(f"undone: {match[0].title}")


@package_app.command("build")
def package_build(
    year: Year = TAX_YEAR,
    out: Annotated[Path | None, typer.Option(help="Folder to create")] = None,
    actor: Actor = ACTOR,
) -> None:
    """Build the folder for the preparer: documents, index.csv, open-items.csv, cover memo."""
    from taxbinder.package.build import build_package

    target = out or Path(f"{year}-package")
    with open_store() as store:
        try:
            result = build_package(store, year, target, actor)
        except FileExistsError as e:
            raise fail(str(e)) from None
    total = sum(result.files.values())
    typer.echo(f"wrote {total} documents and {result.open_items} open items to {result.root}")
    if result.unreadable:
        typer.echo(f"could not read {len(result.unreadable)} document(s); run `taxbinder fsck`")
    typer.echo("The package is not encrypted: share it only through your preparer's portal.")


# -- schemas and developer tools -----------------------------------------------------------


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option("--out", help="Output directory")] = Path("schemas"),
) -> None:
    """Write JSON Schema for every record type."""
    for path in export_schemas(out):
        typer.echo(path)


@dev_app.command("layout")
def dev_layout(doc: Annotated[str, typer.Argument(help="Document id (Documents page)")]) -> None:
    """A document's layout with every digit replaced by 9, to share for tuning the readers.
    Names and words are left as they are: look it over before sharing."""
    from taxbinder.extract.figures import masked_layout

    with open_store() as store:
        d, _ = store.get("document", doc)
        data = store.blobs.get(d.sha256)
    typer.echo(masked_layout(data))
