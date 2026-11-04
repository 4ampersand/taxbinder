#!/usr/bin/env python3
"""Freeze the taxbinder backend (`taxbinder.sidecar`) into a folder (the program and its
libraries) at src-tauri/backend/, which the app bundles as a resource and starts. With
`TAXBINDER_SIGN_IDENTITY` (make release), every library and the program are signed with that
Developer ID and the hardened runtime.

Usage (from the repo root, with the project environment):
    uv run python desktop/scripts/build_sidecar.py [--target <triple>]
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT.parent / "build" / "demo-samples"  # `make samples` (tests/samples/demo.py)
LICENCES = ROOT.parent / "build" / "licences"  # `make licences` (taxbinder/dev/licences.py)
ENTRY = ROOT / "scripts" / "sidecar_entry.py"
BACKEND = ROOT / "src-tauri" / "backend"  # bundled into the app as a resource (tauri.conf.json)
ENTITLEMENTS = ROOT / "src-tauri" / "entitlements.plist"
ENGINE = ROOT.parent / "build" / "engine"  # `make engine` (desktop/scripts/fetch_engine.py)
NAME = "taxbinder-backend"


def host_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("could not determine rust host triple from `rustc -vV`")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="Rust target triple (default: host)")
    args = ap.parse_args()
    triple = args.target or host_triple()

    if not (SAMPLES / "documents").is_dir():
        raise SystemExit("no demo sample files: run `make samples` first")
    if not (ENGINE / "llama-server").is_file():
        raise SystemExit("no reading engine: run `make engine` first")
    if not (LICENCES / "THIRD-PARTY-LICENSES.txt").is_file():
        raise SystemExit("no third-party licences file: run `make licences` first")
    from taxbinder import ocr

    models = ocr.models_dir()
    if models is None:
        raise SystemExit("no text-recognition models: `uv sync` installs them (rapidocr, dev)")
    work = ROOT / "build" / "pyinstaller"
    dist = ROOT / "build" / "dist"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",  # a folder: every library a signed file Apple can check
        "--name",
        NAME,
        "--workpath",
        str(work),
        "--distpath",
        str(dist),
        "--specpath",
        str(work),
        # Loaded dynamically: uvicorn protocols, keyring backends, taxbinder modules.
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "keyring",
        "--collect-submodules",
        "taxbinder",
        # Developer and test tools never go in: they're only reached through test helpers
        # (anyio's and keyring's) or build-time code (cffi), or supply files at build time
        # (rapidocr, with OpenCV: the text-recognition models; PyYAML comes with it, and numpy
        # and uvicorn only try it); the licences file lists what the app includes (checked).
        *(
            arg
            for m in (
                "taxbinder.dev",
                "pytest",
                "_pytest",
                "pluggy",
                "iniconfig",
                "packaging",
                "setuptools",
                "pkg_resources",
                "keyring.testing",
                "anyio.pytest_plugin",
                "cffi.setuptools_ext",
                "rapidocr",
                "cv2",
                "yaml",
            )
            for arg in ("--exclude-module", m)
        ),
        # Data files: templates, static files, rule parameters; pdfminer's font tables.
        "--collect-data",
        "taxbinder",
        # Its own metadata: the version is read from it (taxbinder.__version__, About).
        "--copy-metadata",
        "taxbinder",
        "--collect-data",
        "pdfminer",
        # The demo's sample files, made at build time; the app never makes them.
        "--add-data",
        f"{SAMPLES}:demo-samples",
        # The third-party licences, made at build time.
        "--add-data",
        f"{LICENCES}:licences",
        # The text-recognition models, where `ocr.models_dir()` looks for them.
        *(arg for m in ocr.MODELS for arg in ("--add-data", f"{models / m}:taxbinder/ocr_models")),
        str(ENTRY),
    ]
    wants_x86 = triple.startswith("x86_64")
    if platform.system() == "Darwin" and wants_x86 != (platform.machine() == "x86_64"):
        cmd[4:4] = ["--target-arch", "x86_64" if triple.startswith("x86_64") else "arm64"]
    if identity := os.environ.get("TAXBINDER_SIGN_IDENTITY"):  # make release: Developer ID
        cmd[-1:-1] = ["--codesign-identity", identity, "--osx-entitlements-file", str(ENTITLEMENTS)]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    from taxbinder.dev.licences import check_bundle  # every bundled package is in the list

    check_bundle(work / NAME, LICENCES / "THIRD-PARTY-LICENSES.txt")

    built = dist / NAME  # the folder: the program and its libraries (_internal/)
    if BACKEND.exists():
        shutil.rmtree(BACKEND)
    shutil.copytree(built, BACKEND, symlinks=True)
    # The reading engine beside the program (`engine.server_path`), signed like the rest
    shutil.copytree(ENGINE, BACKEND / "engine", symlinks=True)
    if identity:
        for f in sorted((BACKEND / "engine").iterdir()):
            program = f.suffix == ".dylib" or f.name == "llama-server"
            if program and f.is_file() and not f.is_symlink():
                subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp",
                                "--options", "runtime", "--entitlements", str(ENTITLEMENTS),
                                str(f)], check=True)  # fmt: skip
    size = sum(p.stat().st_size for p in BACKEND.rglob("*") if p.is_file() and not p.is_symlink())
    print(f"backend -> {BACKEND.relative_to(ROOT)}/ ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
