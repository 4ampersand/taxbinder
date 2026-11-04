"""The third-party licences file, made when the app is built (`make licences`,
run by `make sidecar`), and bundled into it: every library the app includes, with its licence
and the licence texts, each text printed once with the packages that use it.

- The backend: Python itself, the runtime requirements of `taxbinder` on this Mac (never the
  development tools), and PyInstaller's bootloader and start-up hooks, frozen into it. After
  freezing, `check_bundle` compares what PyInstaller took with this list.
- The app window: the Rust crates the shell is built from (normal dependencies only, for Apple
  Silicon), from `cargo metadata`.
- The reading engine: llama.cpp's server and libraries, fetched by `make engine`.

A Python package without a licence text stops the build (unless it shares another package's,
in SAME_AS): nothing goes in unlisted. A crate published without its licence file is listed
with its licence's name and a note.

    uv run python -m taxbinder.dev.licences [--out FILE]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from importlib.metadata import Distribution, distribution, distributions
from pathlib import Path

from packaging.requirements import Requirement

from taxbinder.help import LICENCES

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "build" / "licences" / LICENCES  # where the app looks for it, unfrozen
TAURI = ROOT / "desktop" / "src-tauri"
RUST_TARGET = "aarch64-apple-darwin"  # the first release: Apple Silicon
# A package offered under a choice of licences: the one TaxBinder uses it under, shown beside
# what it declares.
CHOSEN = {"dulwich": "Apache-2.0"}
# Packages from one project that ship their licence once, in another of its packages.
SAME_AS: dict[str, str] = {}
# Packages that keep their licence texts beside their code, not in their metadata folder.
IN_PACKAGE = {"onnxruntime": ("onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt")}
# Published without any licence file (checked by hand): listed with the licence they declare.
WITHOUT_FILE = {"flatbuffers"}  # Apache-2.0 (its metadata)
LICENCE_NAME = re.compile(r"(?i)^(licen[cs]e|copying|notice|unlicense|copyright)")
NO_FILE = "(published without its licence file: the licence named applies)"


@dataclass
class Item:
    group: str  # "Python" / "Rust"
    name: str
    version: str
    licence: str
    texts: list[str] = field(default_factory=list)


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _licence_name(d: Distribution) -> str:
    m = d.metadata
    if chosen := CHOSEN.get(_key(m["Name"])):
        declared = m.get("License-Expression") or m.get("License") or ""
        return f"{declared}; used by TaxBinder under {chosen}"
    if expr := m.get("License-Expression"):
        return expr
    if (text := (m.get("License") or "").strip()) and "\n" not in text and len(text) < 120:
        return text
    classifiers = [c.split(" :: ")[-1] for c in m.get_all("Classifier") or []
                   if c.startswith("License ::")]  # fmt: skip
    return ", ".join(classifiers) or "see its licence text"


def _decode(data: bytes) -> str:
    """A licence file's text (a few are Latin-1, not UTF-8)."""
    try:
        return data.decode().strip()
    except UnicodeDecodeError:
        return data.decode("latin-1").strip()


def _platform_tag() -> str:
    return f"{platform.system().lower()}_{platform.machine().lower()}"  # darwin_arm64


def _python_texts(d: Distribution) -> list[str]:
    """The licence files in a package's metadata folder (never its code or tests). A platform's
    own list (PDFium's libraries) only for this platform."""
    texts = []
    for f in d.files or []:
        if str(f) in IN_PACKAGE.get(_key(d.metadata["Name"]), ()):
            texts.append(f"{f.name}\n\n{_decode(Path(str(f.locate())).read_bytes())}")
            continue
        parts = f.parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        inside = "licenses" in parts[1:-1]
        if not (inside or LICENCE_NAME.match(f.name)):
            continue
        if "data" in parts and _platform_tag() not in parts:
            continue  # another platform's libraries
        text = _decode(Path(str(f.locate())).read_bytes())
        texts.append(f"{'/'.join(parts[2:]) if inside else f.name}\n\n{text}")
    return texts


def python_packages(root: str = "taxbinder") -> list[Item]:
    """The runtime requirements of `root` on this Mac, and their own, without the package."""
    seen: dict[str, Distribution] = {}
    todo = [root]
    while todo:
        name = todo.pop()
        if _key(name) in seen:
            continue
        d = seen[_key(name)] = distribution(name)
        for line in d.requires or []:
            req = Requirement(line)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    del seen[_key(root)]  # TaxBinder itself: About says its licence
    items = [Item("Python", d.metadata["Name"], d.version, _licence_name(d), _python_texts(d))
             for d in seen.values()]  # fmt: skip
    by_key = {_key(i.name): i for i in items}
    for item in items:
        if not item.texts and (other := SAME_AS.get(_key(item.name))) in by_key:
            item.texts = by_key[other].texts
    missing = [i.name for i in items if not i.texts and _key(i.name) not in WITHOUT_FILE]
    if missing:
        raise SystemExit(f"no licence text for: {', '.join(sorted(missing))}")
    return sorted(items, key=lambda i: _key(i.name))


def python_itself() -> Item:
    v = sys.version_info
    stdlib = Path(sys.base_prefix) / "lib" / f"python{v.major}.{v.minor}"
    text = (stdlib / "LICENSE.txt").read_text().strip()
    return Item("Python", "Python", platform.python_version(), "PSF-2.0", [f"LICENSE\n\n{text}"])


def bootloader() -> Item:
    d = distribution("pyinstaller")
    note = ("PyInstaller's bootloader is part of the backend's program file. Its licence (GPL "
            "2.0 or later, with an exception for programs it builds) is below.")  # fmt: skip
    name = "GPL-2.0-or-later with the bootloader exception"
    return Item("Python", "PyInstaller bootloader", d.version, name, [note, *_python_texts(d)])


def hooks() -> Item:
    """PyInstaller's community hooks: their start-up hooks run inside the backend."""
    d = distribution("pyinstaller-hooks-contrib")
    return Item("Python", d.metadata["Name"], d.version, _licence_name(d), _python_texts(d))


def ocr_models() -> Item:
    """The text-recognition models: PaddleOCR's, from the rapidocr package (its models
    only; its code and OpenCV stay out of the app)."""
    d = distribution("rapidocr")
    note = ("Only the text-recognition models (PaddleOCR PP-OCRv6 detection and recognition, "
            "Apache-2.0) are included, not this package's code.")  # fmt: skip
    texts = [note, *_python_texts(d)]
    return Item("Python", d.metadata["Name"], d.version, _licence_name(d), texts)


def engine(folder: Path = ROOT / "build" / "engine") -> Item:
    """llama.cpp (the reading engine), as `make engine` fetched it."""
    if not (folder / "LICENSE").is_file():
        raise SystemExit("no reading engine: run `make engine` first")
    release = (folder / "RELEASE").read_text().strip()
    text = f"LICENSE\n\n{_decode((folder / 'LICENSE').read_bytes())}"
    return Item("Engine", "llama.cpp", release, "MIT", [text])


def rust_crates(manifest: Path = TAURI / "Cargo.toml") -> list[Item]:
    out = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--offline", "--manifest-path",
         str(manifest), "--filter-platform", RUST_TARGET],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    meta = json.loads(out)
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen, todo = set(), [root]
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        seen.add(i)
        todo += [d["pkg"] for d in nodes[i]["deps"]
                 if any(k["kind"] is None for k in d["dep_kinds"])]  # fmt: skip
    seen.discard(root)
    items = []
    for i in seen:
        p = packages[i]
        folder = Path(p["manifest_path"]).parent
        files = sorted(f for f in folder.iterdir() if f.is_file() and LICENCE_NAME.match(f.name))
        if p.get("license_file"):
            files.append(folder / p["license_file"])
        texts = [f"{f.name}\n\n{_decode(f.read_bytes())}" for f in dict.fromkeys(files)]
        licence = p.get("license") or "see its licence text"
        items.append(Item("Rust", p["name"], p["version"], licence, texts))
    return sorted(items, key=lambda i: (i.name, i.version))


def render(items: list[Item]) -> str:
    """The list, then each licence text once, with what uses it."""
    texts: dict[str, list[str]] = {}
    lines = [
        "TaxBinder: third-party licences",
        "",
        "TaxBinder is free software under the GNU Affero General Public License, version 3 or",
        "later. It includes the free software below, each under its own licence. This list was",
        "made when this copy of TaxBinder was built, from what each package declares.",
        "",
    ]
    groups = (("Python", "In the backend (Python)"), ("Engine", "In the reading engine"),
              ("Rust", "In the app window (Rust)"))  # fmt: skip
    for group, title in groups:
        mine = [i for i in items if i.group == group]
        lines += [f"{title}: {len(mine)}", ""]
        for i in mine:
            note = "" if i.texts else f" {NO_FILE}"
            lines.append(f"  {i.name} {i.version}: {i.licence}{note}")
            for t in i.texts:
                texts.setdefault(hashlib.sha256(t.encode()).hexdigest(), [t]).append(
                    f"{i.name} {i.version}"
                )
        lines.append("")
    lines += ["", "Licence texts", "=" * 13, ""]
    for text, *users in texts.values():
        lines += ["-" * 78, f"Used by: {', '.join(users)}", "-" * 78, "", text, ""]
    return "\n".join(lines) + "\n"


def build(out: Path = OUT) -> list[Item]:
    items = [python_itself(), *python_packages(), bootloader(), hooks(), ocr_models(),
             engine(), *rust_crates()]  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(items))
    return items


def bundled(work: Path) -> set[str]:
    """The Python distributions PyInstaller put into the backend, from its records in `work`
    (every file it took, mapped to the package that installed it)."""
    owner = {}
    for d in distributions():
        for f in d.files or []:
            owner[str(Path(str(f.locate())).resolve())] = _key(d.metadata["Name"])
    found = set()
    for toc in ("PYZ-00.toc", "PKG-00.toc", "COLLECT-00.toc"):  # COLLECT: a folder build's files
        if not (work / toc).is_file():
            continue
        for part in ast.literal_eval((work / toc).read_text()):
            if not isinstance(part, list):
                continue
            for entry in part:
                if isinstance(entry, tuple) and len(entry) == 3 and entry[1]:
                    if name := owner.get(str(Path(entry[1]).resolve())):
                        found.add(name)
    return found


def listed(path: Path) -> set[str]:
    """The names in a licences file's list."""
    head = path.read_text().split("\nLicence texts\n")[0]
    return {_key(m.group(1)) for m in re.finditer(r"^  (.+?) \S+: ", head, re.M)}


def check_bundle(work: Path, path: Path = OUT) -> None:
    """Stop the build if the backend took a package the licences file doesn't list."""
    covered = {"taxbinder", "pyinstaller"}  # TaxBinder (About); PyInstaller's bootloader entry
    if missing := bundled(work) - listed(path) - covered:
        raise SystemExit(f"bundled but not in the licences file: {', '.join(sorted(missing))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    out = ap.parse_args(argv).out
    items = build(out)
    rust = sum(i.group == "Rust" for i in items)
    print(f"{len(items) - rust} Python and engine, {rust} Rust libraries → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
