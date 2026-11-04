"""Build a small vault with this version of TaxBinder, from the fictional sample household, and
save it as an encrypted backup: `format-<N>.tbak` and its recovery key `format-<N>.key`.

Run once when the vault format changes (store/upgrade.py), *before* raising `FORMAT`, so the
fixture is a vault made by the previous version. Never regenerate or edit an existing fixture:

    uv run python -m tests.fixtures.vaults.make

The key is a test key: it opens only this fictional fixture.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from dulwich.repo import Repo
from typer.testing import CliRunner

from taxbinder import matrix
from taxbinder.cli import app
from taxbinder.entities import list_entities
from taxbinder.models import Taxpayer
from taxbinder.models.matrix import Figure, FigureSet
from taxbinder.store import RecordStore, keys_from_env, open_vault
from taxbinder.store.kinds import KINDS
from taxbinder.store.vault import FORMAT
from tests.samples.generate import generate

HERE = Path(__file__).parent
ACTOR = "fixture"


def cli(*args: object, input: str | None = None) -> str:
    result = CliRunner().invoke(app, [str(a) for a in args], input=input)
    if result.exit_code != 0:
        sys.exit(f"taxbinder {' '.join(map(str, args))} failed:\n{result.output}")
    return result.output


def records(vault: Path, kind: str) -> list:
    with RecordStore(open_vault(vault, keys_from_env())) as store:
        return list(store.list(kind))


def authors(vault: Path) -> set[str]:
    """Who the vault's history says made each change (never the person running this)."""
    with Repo(str(vault / "records")) as repo:
        return {e.commit.author.decode().split(" <")[0] for e in repo.get_walker()}


def build(work: Path) -> tuple[Path, str]:
    """The vault in `work/vault` and its recovery key."""
    vault = work / "vault"
    os.environ |= {"TAXBINDER_VAULT": str(vault), "TAXBINDER_KEYSTORE": "memory"}
    os.environ.pop("TAXBINDER_ENTITY", None)
    recovery = re.search(r"AGE-SECRET-KEY-\S+", cli("init")).group(0)  # type: ignore[union-attr]
    with RecordStore(open_vault(vault, keys_from_env())) as store:
        with store.operation(ACTOR, "household"):
            store.put(Taxpayer(id="primary", name="Avery Quill", tin="900-00-0101"), None)
            store.put(Taxpayer(id="spouse", name="Jordan Quill", tin="900-00-0102"), None)

    samples = work / "samples"
    expected = generate(samples)
    by = {
        (a["doc_type"], a["year"], a["kind"], a["layout"]): a["file"]
        for a in reversed(expected["documents"])
    }
    a = ("--actor", ACTOR)
    cli(
        "row",
        "add",
        "--year",
        2025,
        "--institution",
        "Tamarack Savings Bank",
        "--source",
        "Joint savings",
        "--doc-type",
        "1099_int",
        "--amount",
        "1200.00",
        *a,
    )
    cli(
        "row",
        "add",
        "--year",
        2025,
        "--institution",
        "Brightwater Analytics Inc.",
        "--source",
        "Brightwater Analytics",
        "--doc-type",
        "w2",
        "--owner",
        "primary",
        *a,
    )
    cli(
        "row",
        "add",
        "--year",
        2025,
        "--institution",
        "Alderpoint Timber Fund LP",
        "--source",
        "Alderpoint Timber Fund",
        "--doc-type",
        "k1_1065",
        *a,
    )
    cli(
        "ingest",
        *[
            samples / by[k]
            for k in [
                ("w2", 2025, "text", "tuned"),
                ("1099_int", 2025, "text", "held_out"),
                ("k1_1065", 2025, "text", "tuned"),
                ("k1_1065", 2025, "scan", "tuned"),
            ]
        ],
        *a,
    )
    cli("reconcile", *a)
    cli("acrossyears", "read", *a)
    source = next(s.id for s in records(vault, "source") if s.id.startswith("tamarack"))
    cli("acrossyears", "set", source, "1099_int", 2025, "1200", *a)
    expectation = records(vault, "expectation")[0].id
    cli("verify", expectation, "--note", "amounts match the statement", *a)
    cli(
        "applicability",
        "set",
        records(vault, "expectation")[-1].id,
        "not_issued",
        "--note",
        "the fund said none this year",
        *a,
    )
    doc = next(
        d.id
        for d in records(vault, "document")
        if d.classification and d.classification.doc_type == "w2"
    )
    cli(
        "claim",
        "donation",
        "--payee",
        "Hollis Ferry Literacy Circle",
        "--amount",
        "1500.00",
        "--date",
        "2025-12-03",
        *a,
    )
    cli(
        "claim",
        "payment",
        "--jurisdiction",
        "federal",
        "--amount",
        "4000.00",
        "--date",
        "2025-09-15",
        "--kind",
        "estimated_q3",
        "--year",
        2025,
        *a,
    )
    cli("privacy", "add", "name", "--note", "son", *a, input="Rowan Quill\n")
    cli("doctype", "add", "Form IT-2104", "--folder", "09-Other", "--keyword", "IT-2104", *a)
    cli(
        "bill",
        "add",
        "Town of Hollis Ferry",
        "--kind",
        "property_tax",
        "--installment",
        "5450.00@2025-01-31:1st",
        "--installment",
        "5450.00@2025-07-31:2nd",
        *a,
    )
    cli("redact", doc, *a)
    if findings := records(vault, "findings"):
        for f in findings[0].findings[:1]:
            cli("ack", f.id, "--note", "known, fine", *a)
    cli("year", "new", 2026, "--from", 2025, *a)
    cli("settings", "set", "usual_box.w2", "2", *a)
    with RecordStore(open_vault(vault, keys_from_env())) as store:
        matrix.set_first_year(store, 2024, ACTOR)
        k1 = next(s.id for s in store.list("source") if s.id.startswith("alderpoint"))
        with store.operation(ACTOR, "figures without a checklist row"):
            figure = Figure(source=k1, doc_type="k1_1065", values=[Decimal("-4987"), None])
            store.put(FigureSet(year=2024, figures=[figure]), None)
    cli("entity", "add", "Quill Family Trust", "--form", "1041", *a)
    trust_k1 = next(
        d for d in expected["documents"] if d["person"] == "trust" and d["kind"] == "text"
    )["file"]
    cli("--entity", "quill-family-trust", "ingest", samples / trust_k1, *a)
    return vault, recovery


def main() -> None:
    tbak, key = HERE / f"format-{FORMAT}.tbak", HERE / f"format-{FORMAT}.key"
    if tbak.exists():
        sys.exit(f"{tbak.name} exists; fixtures are never regenerated")
    with tempfile.TemporaryDirectory() as tmp:
        vault, recovery = build(Path(tmp))
        vaults = [ref.path for ref in list_entities(vault)]
        missing = [k for k in KINDS if not any(records(v, k) for v in vaults)]
        if missing:
            sys.exit(f"the fixture has no {', '.join(missing)} records; add them to make.py")
        if others := {author for v in vaults for author in authors(v)} - {ACTOR, "taxbinder"}:
            sys.exit(f"changes recorded by {others}: pass --actor {ACTOR} to every command")
        cli("backup", "create", "--out", Path(tmp) / "out")
        made = next((Path(tmp) / "out").glob("*.tbak"))
        tbak.write_bytes(made.read_bytes())
    key.write_text(f"# Test key: opens only the fictional fixture {tbak.name}.\n{recovery}\n")
    print(f"wrote {tbak.name} and {key.name}")


if __name__ == "__main__":
    main()
