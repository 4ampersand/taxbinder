"""The release build's set-up: one version everywhere, the backend bundled as a folder,
the hardened runtime with an entitlements file, and the script's check of what must be signed.
The signing and notarization themselves run in `make release`, with the maintainer's certificate."""

from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
DESKTOP = ROOT / "desktop"
sys.path.insert(0, str(DESKTOP / "scripts"))

import release  # noqa: E402


def test_one_version_everywhere() -> None:
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)  # type: ignore[union-attr]
    cargo = re.search(
        r'^version = "([^"]+)"', (DESKTOP / "src-tauri" / "Cargo.toml").read_text(), re.M
    ).group(1)  # type: ignore[union-attr]
    tauri = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())["version"]
    npm = json.loads((DESKTOP / "package.json").read_text())["version"]
    assert py == cargo == tauri == npm == release.version()


def test_a_build_number_for_about() -> None:
    """macOS's About window shows the build in brackets; without one it repeats the version."""
    conf = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())
    build = conf["bundle"]["macOS"]["bundleVersion"]
    assert build.isdigit() and int(build) >= 1


def test_the_backend_is_a_bundled_folder_under_the_hardened_runtime() -> None:
    conf = json.loads((DESKTOP / "src-tauri" / "tauri.conf.json").read_text())
    assert (
        conf["bundle"]["resources"] == {"backend/": "backend/"}
        and "externalBin" not in conf["bundle"]
    )
    mac = conf["bundle"]["macOS"]
    assert mac["hardenedRuntime"] is True and mac["minimumSystemVersion"] == "13.0"
    entitlements = plistlib.loads((DESKTOP / "src-tauri" / mac["entitlements"]).read_bytes())
    assert isinstance(entitlements, dict)  # every key in it says why (see the file)
    assert '"backend/taxbinder-backend"' in (DESKTOP / "src-tauri" / "src" / "lib.rs").read_text()


def test_what_must_be_signed(tmp_path: Path) -> None:
    (tmp_path / "lib.so").write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 12)  # a 64-bit Mach-O
    (tmp_path / "fat.dylib").write_bytes(b"\xca\xfe\xba\xbe" + b"\0" * 12)  # a universal one
    (tmp_path / "data.txt").write_text("not a program")
    (tmp_path / "link.so").symlink_to(tmp_path / "lib.so")  # signed through what it points to
    assert sorted(p.name for p in release.machos(tmp_path)) == ["fat.dylib", "lib.so"]
