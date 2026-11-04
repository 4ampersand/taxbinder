"""4Ampersand's model list signature: run by the maintainer, never by the app (the tests use a
stand-in Keychain).

    uv run python -m taxbinder.dev.model_list keygen     # once: the key into the Keychain
    uv run python -m taxbinder.dev.model_list sign FILE  # FILE.sig, to publish beside FILE

`keygen` keeps the private key in this Mac's Keychain (service below) and prints only the public
key, which goes into `llm.PUBLIC_KEY`. Losing the private key means a new one and a new app
release before newer lists can be published; it's never put in the repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVICE = "com.4ampersand.taxbinder.model-list"
ACCOUNT = "ed25519"


def keygen() -> str:
    import keyring
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if keyring.get_password(SERVICE, ACCOUNT):
        raise SystemExit("a key is in the Keychain already: not replaced")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())  # fmt: skip
    keyring.set_password(SERVICE, ACCOUNT, raw.hex())
    public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw)  # fmt: skip
    return public.hex()


def sign(path: Path) -> Path:
    import keyring
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    secret = keyring.get_password(SERVICE, ACCOUNT)
    if not secret:
        raise SystemExit("no key in the Keychain: run keygen first")
    from taxbinder import llm

    llm.parse(__import__("json").loads(path.read_text()))  # never sign a list the app can't use
    out = path.with_name(path.name + ".sig")
    out.write_bytes(
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret)).sign(path.read_bytes())
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen")
    s = sub.add_parser("sign")
    s.add_argument("file", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        print(f"public key (for llm.PUBLIC_KEY): {keygen()}")
    else:
        print(f"signature: {sign(args.file)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
