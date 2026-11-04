#!/usr/bin/env python3
"""Fetch the reading engine: llama.cpp's `llama-server` and its libraries, from the
pinned release on GitHub, checked against the checksum written here, into build/engine/.

    make engine            (uv run python desktop/scripts/fetch_engine.py)

`make sidecar` copies it into the app's backend folder (and `make release` signs it there).
Nothing else from the release goes in: its other programs (chat, benchmarks, conversion) aren't
needed. To move to a newer release, change RELEASE and the checksums, and run the tests.
"""

from __future__ import annotations

import hashlib
import io
import platform
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "engine"
RELEASE = "b11801"
# (file on the release page, its SHA-256) per platform
FILES = {
    ("Darwin", "arm64"): (
        f"llama-{RELEASE}-bin-macos-arm64.tar.gz",
        "2b6f5e526ff462d0817eb670e0774715cc61f8016a3cb98ade7d2b283a279af1",
    ),
}
URL = "https://github.com/ggml-org/llama.cpp/releases/download/{release}/{file}"
KEEP = ("llama-server", "LICENSE")  # and the libraries the server loads (below)


def wanted(name: str) -> bool:
    """The server, its licence, and its libraries: llama, mtmd (images), ggml and its back ends,
    the server's own; not the other programs' (`libllama-bench-impl` …)."""
    if name in KEEP:
        return True
    if not (name.startswith("lib") and ".dylib" in name):
        return False
    return not name.endswith("-impl.dylib") or name == "libllama-server-impl.dylib"


def main() -> int:
    key = (platform.system(), platform.machine())
    if key not in FILES:
        raise SystemExit(f"no reading engine pinned for {key[0]} {key[1]} yet")
    file, digest = FILES[key]
    if (OUT / "llama-server").is_file() and (OUT / "RELEASE").read_text().strip() == RELEASE:
        print(f"engine {RELEASE} already in {OUT.relative_to(ROOT)}/")
        return 0
    url = URL.format(release=RELEASE, file=file)
    print(f"+ download {url}", flush=True)
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"{file}: the checksum doesn't match: not used")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if not wanted(name) or not (member.isfile() or member.issym()):
                continue
            target = OUT / name
            if member.issym():
                target.symlink_to(Path(member.linkname).name)  # libggml.dylib -> libggml.0.dylib
            else:
                target.write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
                target.chmod(0o755 if name == "llama-server" else 0o644)
    (OUT / "RELEASE").write_text(RELEASE + "\n")
    size = sum(p.stat().st_size for p in OUT.iterdir() if p.is_file() and not p.is_symlink())
    print(f"engine {RELEASE} -> {OUT.relative_to(ROOT)}/ ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
