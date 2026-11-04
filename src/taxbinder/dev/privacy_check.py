"""Privacy check: nothing from your own vault in this repository.

Looks for every value the vault knows (names, SSNs/EINs, account numbers and the values marked
private, in the forms redaction looks for: `redact/detect.py`) plus the names of institutions
(not tax agencies) and sources (a fund, a property; not generic ones such as "Joint savings"),
and for SSN- or EIN-shaped numbers outside the fictional ranges. Tax agencies, a number that's
only a year, and a number of four digits or fewer (a form's: "5498-SA") say nothing about the
household and aren't looked for; four digits are still found as "…5498" or "ending in 5498". It
looks in every tracked file (and the text inside .xlsx workbooks), every version of every file in
history, and commit messages and author names. The maintainer's own name (git `user.name`) is
allowed only as author: in commits and in the `AUTHORSHIP` files (a copyright line).

Run by you (`make privacy`), never by tests on the real vault. It reads a temporary copy of
the vault's records, so nothing in the real vault can change. Hits are reported by place and
placeholder (`PERSON_02`), never by value. Without a vault (a contributor), only the SSN/EIN shapes
are checked. Exit status: 0 clean, 1 hits, 2 a vault is there but can't be read.

    uv run python -m taxbinder.dev.privacy_check [--vault DIR] [--repo DIR]
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from taxbinder.entities import HOUSEHOLD, list_entities
from taxbinder.redact.detect import B, E, Finder, known_finders
from taxbinder.redact.tokens import Pseudonyms
from taxbinder.states import table
from taxbinder.store import RecordStore, keys_from_env, open_vault
from taxbinder.store.keys import KeyStore
from taxbinder.store.vault import REAL_VAULT

# Where the maintainer's own name belongs (git `user.name`); anywhere else it's a hit
AUTHORSHIP = {
    "CLA.md", "NOTICE", "README.md", "pyproject.toml", "src/taxbinder/web/templates/help.html",
}  # fmt: skip
# Tax agencies are in every tax document and say nothing about the household
AGENCIES = {
    "irs", "internal revenue service", "department of the treasury",
    "u.s. department of the treasury", "us treasury", "u.s. treasury", "treasury", "eftps",
    "electronic federal tax payment system", "social security administration", "ssa", "ftb",
    "franchise tax board", "california franchise tax board",
    *(f"state of {s.name}".casefold() for s in table().values()),
}  # fmt: skip
# Numbers from the vault: a match that's only a year ("2026") is everywhere in tax code, and so
# are four digits or fewer (a form's number, "5498-SA"); "…5498" or "ending in 5498" is a hit
NUMBERS = {"TIN", "EIN", "ACCT", "NUM", "PHONE"}
YEAR = re.compile(r"(?:19|20)\d\d")
MASKED = re.compile(r"[Xx*•#…]|ending", re.I)  # how a match of the last four digits starts
# Words that make up generic account names ("Joint savings", "Consolidated Tax Statement"): a
# source name made only of these says nothing about the household and is everywhere in tax code
GENERIC = set(
    "a an and the of for from to in on with account accounts brokerage savings checking joint "
    "individual trust ira roth rollover sep 401k 403b 529 hsa fsa plan stock stockplan options "
    "supplemental premium pledged loc mortgage interest income dividends tax statement "
    "consolidated benefit verification letter payslip deferred comp compensation withdrawal "
    "fund funds llc lp inc corp co ltd w2 w-2 k1 k-1 1098 1099 1099-b 1099-int 1099-div 5498 "
    "retirement pension annuity bank card credit insurance policy".split()
)

# Shapes that are never fictional here: fictional SSNs start 900-, fictional EINs 00-
BUILT_IN = [
    Finder(re.compile(rf"{B}(?!900-)\d{{3}}-\d{{2}}-\d{{4}}{E}"), "SSN-shaped", "SSN-shaped"),
    Finder(re.compile(rf"{B}(?!00-)\d{{2}}-\d{{7}}{E}"), "EIN-shaped", "EIN-shaped"),
]


@dataclass(frozen=True)
class Hit:
    place: str  # "path:line", "path:line (earlier version abc1234)", "commit abc1234 message"
    label: str  # placeholder or built-in shape; never the value


def vault_finders(vault: Path, keys: KeyStore) -> list[Finder]:
    """Searches for what the household's vault and every entity's vault know, built from a copy."""
    finders: list[Finder] = []
    with tempfile.TemporaryDirectory() as tmp:
        for ref in list_entities(vault):
            copy = Path(tmp) / ref.slug
            shutil.copytree(ref.path / "records", copy / "records")
            tag = "" if ref.slug == HOUSEHOLD else f"{ref.slug}:"
            with RecordStore(open_vault(copy, keys)) as store:
                tokens = Pseudonyms(store)
                found = known_finders(store, tokens)
                for inst in store.list("institution"):
                    if inst.kind == "government" or inst.name.casefold() in AGENCIES:
                        continue
                    token = tokens.token("INST", inst.name)
                    regex = re.compile(B + r"\s+".join(map(re.escape, inst.name.split())) + E, re.I)
                    found.append(Finder(regex, "INST", token, True))
                for src in store.list("source"):
                    if src.name.casefold() in AGENCIES:
                        continue
                    words = re.findall(r"[\w-]+", src.name.casefold())
                    if not words or all(w in GENERIC or w.isdigit() for w in words):
                        continue
                    token = tokens.token("SRC", src.name)
                    regex = re.compile(B + r"\s+".join(map(re.escape, src.name.split())) + E, re.I)
                    found.append(Finder(regex, "SRC", token, True))
            for f in found:
                regex = f.regex
                if f.prefix == "NAME":  # a single word of a name: as written, so "mark" in code
                    regex = re.compile(regex.pattern)  # doesn't count as the name "Mark"
                finders.append(Finder(regex, f.prefix, tag + (f.token or f.prefix), True))
    return finders


def _git(repo: Path, *args: str, data: bytes | None = None, ok: tuple[int, ...] = (0,)) -> bytes:
    run = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True)
    if run.returncode not in ok:
        raise subprocess.CalledProcessError(run.returncode, run.args, run.stdout, run.stderr)
    return run.stdout


def _text(path: str, data: bytes) -> str | None:
    """The text to search: a workbook's XML with the tags taken out; None for other binaries."""
    if path.endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        return re.sub(r"<[^>]+>", " ", xml)
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", "replace")


def _search(text: str, where: str, finders: list[Finder]) -> list[Hit]:
    hits: set[Hit] = set()
    for f in finders:
        for m in f.regex.finditer(text):
            digits = re.sub(r"\D", "", m.group())
            if f.prefix in NUMBERS and (
                YEAR.fullmatch(digits) or (len(digits) <= 4 and not MASKED.match(m.group()))
            ):
                continue
            line = text.count("\n", 0, m.start()) + 1
            hits.add(Hit(where.format(line=line), f.token or f.prefix))
    return sorted(hits, key=lambda h: (h.place, h.label))


def check(repo: Path, finders: list[Finder]) -> list[Hit]:
    hits: list[Hit] = []
    me = _git(repo, "config", "user.name", ok=(0, 1)).decode().strip()
    others = [f for f in finders if not (me and f.regex.search(me))]  # all but the author's name
    current: set[str] = set()  # blob ids of the tracked files as they are now
    for row in _git(repo, "ls-files", "-s", "-z").split(b"\0"):
        if not row:
            continue
        meta, path = row.decode().split("\t", 1)
        current.add(meta.split()[1])
        file = repo / path
        if file.is_file() and (text := _text(path, file.read_bytes())) is not None:
            hits += _search(text, f"{path}:{{line}}", others if path in AUTHORSHIP else finders)

    paths: dict[str, str] = {}
    for line in _git(repo, "rev-list", "--all", "--objects").decode().splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in current:
            paths.setdefault(sha, path)
    blobs = [s for s, t in zip(paths, _types(repo, list(paths)), strict=True) if t == "blob"]
    for sha in blobs:
        path = paths[sha]
        if (text := _text(path, _git(repo, "cat-file", "blob", sha))) is not None:
            hits += _search(text, f"{path}:{{line}} (earlier version {sha[:7]})", finders)

    log = _git(repo, "log", "--all", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e").decode()
    for entry in log.split("\x1e"):
        if entry.strip():
            sha, author, committer, message = entry.strip("\n").split("\0", 3)
            hits += _search(f"{author}\n{committer}", f"commit {sha[:7]} author", others)
            hits += _search(message, f"commit {sha[:7]} message", finders)
    return hits


def _types(repo: Path, shas: list[str]) -> list[str]:
    if not shas:
        return []
    out = _git(repo, "cat-file", "--batch-check=%(objecttype)", data="\n".join(shas).encode())
    return out.decode().split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--vault", type=Path, default=REAL_VAULT, help="default: the real vault")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    vault = args.vault.expanduser()
    if not (vault / "records" / "vault.yaml").exists():  # a contributor without TaxBinder data
        print(f"privacy check: no vault at {args.vault}; only the built-in checks run")
        hits = check(args.repo, BUILT_IN)
        for h in hits:
            print(f"{h.place}  {h.label}")
        return 1 if hits else 0
    try:
        finders = vault_finders(vault, keys_from_env())
    except Exception as e:  # noqa: BLE001 - any reason the vault can't be read stops the check
        print(f"privacy check: can't read the vault at {args.vault}: {type(e).__name__}")
        print("Quit TaxBinder and try again; macOS may ask to allow access to the vault key.")
        return 2
    hits = check(args.repo, finders + BUILT_IN)
    for h in hits:
        print(f"{h.place}  {h.label}")
    kinds = len({f.token for f in finders})
    if hits:
        print(f"privacy check: {len(hits)} hit(s); {kinds} values from the vault searched")
        return 1
    print(f"privacy check: clean; {kinds} values from the vault searched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
