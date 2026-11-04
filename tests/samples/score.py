"""Score the app's reader on the sample set: per document, are the
type, year and issuer all right? Reported apart for held-out layouts, scans and photos; figures
and account endings too; and whose each one is (right, asked on review, or wrong: target none).

    uv run python -m tests.samples.generate OUT_DIR
    uv run python -m tests.samples.score OUT_DIR [--institutions]

`--institutions` first tells the reader every issuer's name (a filled-in vault); without it the
vault starts empty, as on a new user's first upload, and an issuer read from a form
becomes an institution as ingest does it: right when every document of one issuer lands on the
same institution.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from ruamel.yaml import YAML

from taxbinder import ocr
from taxbinder.extract.figures import LABELS, read_boxes
from taxbinder.ingest.classify import classify
from taxbinder.ingest.inbox import needs_ocr
from taxbinder.ingest.pdftext import pdf_info, sniff_mime
from taxbinder.institutions import name_key, remember
from taxbinder.models import Institution, Taxpayer
from taxbinder.returns import with_reading
from taxbinder.slack import digits
from taxbinder.whose import recipient_of

from .household import ISSUERS, PEOPLE, TRUST

# The household as Check confirms it: the people, and the trust that holds accounts.
# what the return lines are called in expected.yaml (forms) and in the app (kinds of income)
TOTALS = {
    "w2": "wages", "1099_int": "interest", "1099_div": "dividends", "1099_r": "retirement",
    "ssa_1099": "social_security", "1098": "mortgage_interest",
}  # fmt: skip
TAXPAYERS = [Taxpayer(id=p.id, name=p.name, tin=p.ssn) for p in PEOPLE.values()] + [
    Taxpayer(id=TRUST.id, name=TRUST.name, kind="trust")
]

LATEST_YEAR = 2026


def _group(answer: dict) -> str:
    return answer["kind"] + (" (held-out layout)" if answer["layout"] == "held_out" else "")


def score(folder: Path, with_institutions: bool = False) -> dict:
    expected = YAML(typ="safe").load((folder / "expected.yaml").read_text())
    institutions = (
        [Institution(id=i.id, name=i.name, kind=i.kind) for i in ISSUERS.values()]
        if with_institutions
        else []
    )
    # whose institution each id is: an empty vault fills up as documents arrive, like ingest
    owner = {i.id: i.id for i in institutions}
    groups: dict[str, Counter] = defaultdict(Counter)
    types: dict[str, Counter] = defaultdict(Counter)
    figures: Counter = Counter()
    read_returns: Counter = Counter()  # what returns say
    for a in expected["documents"]:
        data = (folder / a["file"]).read_bytes()
        mime = sniff_mime(data)
        text = pdf_info(data)[1] if mime == "application/pdf" else ""
        pages = ocr.read(data, mime) if needs_ocr(mime, text) else []  # scans, photos
        if pages:
            text = ocr.text_of(pages)
        c = classify(text, a["file"], institutions, LATEST_YEAR, data=data, pages=pages or None)
        issuer = c.issuer
        if issuer is None:
            issuer, new = remember(c.issuer_name, c.issuer_tin, c.doc_type, institutions, c.scanned)
            if new is not None:
                institutions = [i for i in institutions if i.id != new.id] + [new]
                owner.setdefault(new.id, a["issuer"])  # the first document names it
        is_return = a["doc_type"] == "tax_return"
        right = {
            "type": c.doc_type == a["doc_type"],
            "year": c.tax_year == a["year"],
            # a return's issuer is its filer
            "issuer": is_return or (issuer is not None and owner.get(issuer) == a["issuer"]),
        }
        right["all three"] = all(right.values())
        if is_return and c.tax_return is not None:
            _score_return(read_returns, a, with_reading(c.tax_return, data))
        g = groups[_group(a)]
        g["documents"] += 1
        g.update(k for k, ok in right.items() if ok)
        if not is_return:  # whose it is: right, asked on review, or wrong
            r = recipient_of(c.recipient_name, c.recipient_last4, TAXPAYERS, c.scanned)
            if not c.recipient_name:
                g["whose not read"] += 1
            elif r.ask or not r.taxpayers:
                g["whose asked"] += 1
            else:
                g["whose right" if a["person"] in r.taxpayers else "whose wrong"] += 1
        if a.get("account"):
            g["accounts"] += 1
            ending = re.sub(r"\D", "", digits(c.account_number or ""))[-4:]
            g["account right"] += ending == a["account"]
        if a["kind"] == "text":
            types[a["doc_type"]]["documents"] += 1
            types[a["doc_type"]]["all three"] += right["all three"]
        if a["kind"] == "text" and a["doc_type"] in LABELS and a.get("figures"):
            found = read_boxes(data, a["doc_type"], list(a["figures"]))
            for box, value in a["figures"].items():
                figures["boxes"] += 1
                got = found.get(box)
                if value is None:
                    figures["blank right"] += got is None
                    figures["blank read as a number"] += got is not None
                else:
                    figures["right"] += got is not None and got.value == Decimal(value)
        elif pages and a["doc_type"] in LABELS and a.get("figures"):  # scans: suggestions to check
            found = read_boxes(data, a["doc_type"], list(a["figures"]), pages)
            for box, value in a["figures"].items():
                if value is not None:
                    got = found.get(box)
                    figures["scan filled"] += 1
                    figures["scan right"] += got is not None and got.value == Decimal(value)
                    figures["scan wrong"] += got is not None and got.value != Decimal(value)
    others = []
    for o in expected["others"]:
        data = (folder / o["file"]).read_bytes()
        mime = sniff_mime(data)
        pages = pdf_info(data)[0] if mime == "application/pdf" else None
        others.append((o["file"], o["problem"], mime, pages))
    return {
        "groups": groups,
        "types": types,
        "figures": figures,
        "returns": read_returns,
        "others": others,
    }


def _score_return(n: Counter, a: dict, info) -> None:  # noqa: ANN001 - a ReturnInfo
    """Form, state and resident on every return; on a 1040 what it says inside."""
    n["returns"] += 1
    n["form right"] += info.form == a["form"]
    state = info.jurisdiction if info.jurisdiction != "federal" else None
    if "resident" in a:  # a state return
        n["state returns"] += 1
        n["state return right"] += state == a["state"] and info.resident == a["resident"]
    if "filing_status" not in a:
        return
    if a["kind"] != "text":
        n["1040 scans"] += 1
        n["1040 scans not read"] += info.read is None  # not read inside yet: never misread
        return
    read = info.read
    n["1040s"] += 1
    if read is None:
        return
    n["filing status right"] += read.filing_status == a["filing_status"]
    n["state right"] += read.state == a["state"]
    n["people right"] += {p.role: p.name for p in read.people} == a["names"]
    ids = {name_key(i.name): i.id for i in ISSUERS.values()}
    got = [ids.get(name_key(x.name)) for x in read.listed]
    n["named"] += len(a["named_sources"])
    n["named found"] += len(set(got) & set(a["named_sources"]))
    n["named wrong"] += sum(g not in a["named_sources"] for g in got)
    n["totals right"] += set(read.totals_only) == {TOTALS[r] for r in a["general_rows"]}


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:3.0f}%" if whole else "   -"


def report(result: dict) -> str:
    cols = ["type", "year", "issuer", "all three"]
    lines = [f"{'documents':28} {'n':>4}  " + "  ".join(f"{c:>9}" for c in cols) + "   account"]
    for name, g in sorted(result["groups"].items()):
        n = g["documents"]
        lines.append(
            f"{name:28} {n:4}  "
            + "  ".join(f"{_pct(g[c], n):>9}" for c in cols)
            + f"   {_pct(g['account right'], g['accounts']):>7}"
        )
    lines += ["", "all three right, text documents by type:"]
    for name, t in sorted(result["types"].items()):
        lines.append(f"  {name:20} {t['all three']:3} / {t['documents']}")
    f, r = result["figures"], result["returns"]
    lines += [
        "",
        f"figures (text documents): {f['right']} right of "
        f"{f['boxes'] - f['blank right'] - f['blank read as a number']} "
        f"filled boxes; blank boxes: {f['blank right']} left blank, "
        f"{f['blank read as a number']} read as a number",
        f"figures (scans and photos, suggested for checking): {f['scan right']} right, "
        f"{f['scan wrong']} wrong of {f['scan filled']} filled boxes",
        "whose (person or trust; the household as Check confirms it):",
        *(
            f"  {name:26} {g['whose right']:3} right, {g['whose asked']:3} asked on review, "
            f"{g['whose wrong']:3} wrong, {g['whose not read']:3} not read"
            for name, g in sorted(result["groups"].items())
        ),
        "",
        "returns: "
        f"{r['form right']} of {r['returns']} forms right; state returns "
        f"{r['state return right']} of {r['state returns']} (state and resident); 1040s: "
        f"filing status {r['filing status right']}, people {r['people right']}, "
        f"state {r['state right']}, totals-only rows {r['totals right']} of {r['1040s']}; "
        f"named payers and entities {r['named found']} found of {r['named']}, "
        f"{r['named wrong']} wrong; scanned 1040s left unread {r['1040 scans not read']} of "
        f"{r['1040 scans']}",
        "",
        "other files:",
    ]
    for file, problem, mime, pages in result["others"]:
        readable = "no text layer" if pages is None else f"{pages} page(s)"
        lines.append(f"  {file:24} {problem:12} {mime:26} {readable}")
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit("usage: python -m tests.samples.score SAMPLE_DIR [--institutions]")
    print(report(score(Path(args[0]), "--institutions" in sys.argv)))
