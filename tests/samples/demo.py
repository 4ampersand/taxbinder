"""The demo's sample files, made when the app is built, never by the app: last
year's return (the demo sets itself up from it) and this year's documents to add: the forms,
a scan, a photo and a damaged file. All from the fictional sample set (generate.py).

    uv run python -m tests.samples.demo OUT_DIR
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from .generate import generate

YEAR = 2025


def build(out: Path) -> Path:
    """Write `out/return/` and `out/documents/` (replacing what's there); returns `out`."""
    with tempfile.TemporaryDirectory() as tmp:
        every = Path(tmp)
        expected = generate(every)
        docs = expected["documents"]
        ret = next(
            a["file"] for a in docs
            if a.get("form") == "1040" and a["year"] == YEAR - 1 and a["kind"] == "text"
        )  # fmt: skip
        mine = [a["file"] for a in docs if a.get("year") == YEAR and a["kind"] == "text"
                and not a.get("form")]  # fmt: skip
        scan = next(a["file"] for a in docs if a["kind"] == "scan" and a.get("year") == YEAR)
        photo = next(a["file"] for a in docs if a["kind"] == "photo" and a.get("year") == YEAR)
        shutil.rmtree(out, ignore_errors=True)
        (out / "return").mkdir(parents=True)
        (out / "documents").mkdir()
        shutil.copyfile(every / ret, out / "return" / f"{YEAR - 1} Form 1040.pdf")
        for name in [*mine, scan, photo, "statement.pdf"]:  # statement.pdf: a damaged file
            shutil.copyfile(every / name, out / "documents" / name)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m tests.samples.demo OUT_DIR")
    print(build(Path(sys.argv[1])))
