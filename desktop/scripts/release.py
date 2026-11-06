#!/usr/bin/env python3
"""A release build: TaxBinder.app and TaxBinder.dmg, signed with the maintainer's
Developer ID, the hardened runtime, notarized by Apple and stapled, in build/release/.

    make release            (uv run python desktop/scripts/release.py)

Needs, once, on this Mac (the maintainer's own steps, never handled here):
- a "Developer ID Application" certificate in the login Keychain;
- notarization credentials saved as the Keychain profile `taxbinder-notary`
  (`xcrun notarytool store-credentials taxbinder-notary …`).
Without either it stops before building, and says which. Nothing secret is printed: the
certificate is named, the profile is used by its name only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
TAURI = DESKTOP / "src-tauri"
APP = TAURI / "target" / "release" / "bundle" / "macos" / "TaxBinder.app"
OUT = ROOT / "build" / "release"
PROFILE = "taxbinder-notary"


def run(*cmd: str, env: dict | None = None, cwd: Path | None = None) -> str:
    print("+", " ".join(cmd), flush=True)
    done = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"{cmd[0]} failed:\n{done.stdout}{done.stderr}")
    return done.stdout + done.stderr


def identity() -> str:
    """The Developer ID Application certificate's name (one, or the one TAXBINDER_SIGN_IDENTITY
    names)."""
    found = re.findall(r'"(Developer ID Application: [^"]+)"',
                       run("security", "find-identity", "-v", "-p", "codesigning"))  # fmt: skip
    wanted = os.environ.get("TAXBINDER_SIGN_IDENTITY")
    if wanted:
        found = [f for f in found if f == wanted]
    if not found:
        sys.exit("no Developer ID Application certificate in the Keychain "
                 "(Xcode → Settings → Accounts → Manage Certificates)")  # fmt: skip
    if len(found) > 1:
        sys.exit(f"several Developer ID certificates: set TAXBINDER_SIGN_IDENTITY to one: {found}")
    return found[0]


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)  # type: ignore[union-attr]


def machos(folder: Path) -> list[Path]:
    """Every program and library in a folder (what must carry a signature)."""
    out = []
    for p in folder.rglob("*"):
        if p.is_file() and not p.is_symlink():
            with p.open("rb") as f:
                if f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe"):
                    out.append(p)
    return out


def notarize(path: Path) -> None:
    """Send to Apple's notary service, wait, then staple the ticket; on refusal, Apple's log."""
    out = subprocess.run(["xcrun", "notarytool", "submit", str(path), "--keychain-profile",
                          PROFILE, "--wait"], capture_output=True, text=True)  # fmt: skip
    print(out.stdout, flush=True)
    if "status: Accepted" not in out.stdout:
        sub = re.search(r"id: ([0-9a-f-]{36})", out.stdout)
        if sub:
            print(run("xcrun", "notarytool", "log", sub.group(1), "--keychain-profile", PROFILE))
        sys.exit(f"notarization of {path.name} wasn't accepted {out.stderr}".strip())


def main() -> int:
    who = identity()
    run("xcrun", "notarytool", "history", "--keychain-profile", PROFILE)  # the profile is there
    print(f"signing as {who}, version {version()}", flush=True)
    env = {**os.environ, "TAXBINDER_SIGN_IDENTITY": who}

    # 1. The backend, every library signed (PyInstaller signs what it collects)
    run("make", "sidecar", env=env, cwd=ROOT)
    for p in machos(TAURI / "backend"):
        run("codesign", "--verify", "--strict", str(p))
    # 2. The app, signed by Tauri with the hardened runtime and entitlements.plist
    run("npx", "tauri", "build", "--bundles", "app", env={**env, "APPLE_SIGNING_IDENTITY": who},
        cwd=DESKTOP)  # fmt: skip
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP))
    # 3. Notarize and staple the app, then the .dmg made from it
    OUT.mkdir(parents=True, exist_ok=True)
    app = OUT / APP.name
    if app.exists():
        shutil.rmtree(app)
    run("ditto", str(APP), str(app))
    archive = OUT / "TaxBinder.zip"
    run("ditto", "-c", "-k", "--keepParent", str(app), str(archive))
    notarize(archive)
    archive.unlink()
    run("xcrun", "stapler", "staple", str(app))
    dmg = OUT / "TaxBinder.dmg"  # the download page links to this name
    dmg.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", "TaxBinder", "-srcfolder", str(app), "-ov", "-format",
        "UDZO", str(dmg))  # fmt: skip
    run("codesign", "--sign", who, "--timestamp", str(dmg))
    notarize(dmg)
    run("xcrun", "stapler", "staple", str(dmg))
    # 4. As Gatekeeper on another Mac would see them
    print(run("spctl", "--assess", "--type", "execute", "--verbose=2", str(app)))
    print(run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
              "--verbose=2", str(dmg)))  # fmt: skip
    print(f"release: {app.relative_to(ROOT)} and {dmg.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
