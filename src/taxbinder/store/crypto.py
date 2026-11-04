"""age encryption for identifier fields and blobs (§4.5, §4.6).

Everything is encrypted to every recipient in `vault.yaml` (the vault key and the offline
recovery key), and decrypted with whichever identity this machine holds.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from pyrage import decrypt, encrypt, x25519

ENCRYPTED_FIELDS = frozenset(
    {
        "number",
        "account_number",
        "tin",
        "issuer_tin",
        "ssn",
        "ein",
        "routing_number",
        "confirmation_number",
        "secret",  # private details and the redaction placeholder map
    }
)
PREFIX, SUFFIX = "ENC[age:", "]"


class WrongKey(Exception):
    """This identity can't decrypt the vault."""


@dataclass(frozen=True)
class Crypto:
    identity: x25519.Identity
    recipients: tuple[x25519.Recipient, ...]

    @classmethod
    def load(cls, identity: str, recipients: list[str]) -> Crypto:
        return cls(
            x25519.Identity.from_str(identity),
            tuple(x25519.Recipient.from_str(r) for r in recipients),
        )

    def seal(self, data: bytes) -> bytes:
        return encrypt(data, list(self.recipients))

    def open(self, data: bytes) -> bytes:
        try:
            return decrypt(data, [self.identity])
        except Exception as e:  # pyrage.DecryptError
            raise WrongKey("this key can't decrypt the vault") from e

    def seal_text(self, text: str) -> str:
        return PREFIX + base64.b64encode(self.seal(text.encode())).decode() + SUFFIX

    def open_text(self, value: str) -> str:
        return self.open(base64.b64decode(value[len(PREFIX) : -len(SUFFIX)])).decode()

    @property
    def fields(self) -> AgeFieldCodec:
        return AgeFieldCodec(self)

    @property
    def blobs(self) -> AgeBlobCodec:
        return AgeBlobCodec(self)


def is_sealed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX) and value.endswith(SUFFIX)


class AgeFieldCodec:
    """Encrypts identifier fields anywhere in a record, in place."""

    def __init__(self, crypto: Crypto) -> None:
        self.crypto = crypto

    def encode(self, data: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        return self._encode(data, previous)

    def decode(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._decode(data)

    def _seal(self, value: str, old: Any) -> str:
        # Values in memory are always plaintext (even one that looks like ENC[...]).
        # Same plaintext as before → keep the old ciphertext, so re-saving doesn't churn diffs.
        if is_sealed(old) and self.crypto.open_text(old) == value:
            return old
        return self.crypto.seal_text(value)

    def _encode(self, node: Any, old: Any) -> Any:
        if isinstance(node, dict):
            olds = old if isinstance(old, dict) else {}
            return {
                k: self._seal(v, olds.get(k))
                if k in ENCRYPTED_FIELDS and isinstance(v, str)
                else self._encode(v, olds.get(k))
                for k, v in node.items()
            }
        if isinstance(node, list):
            olds = old if isinstance(old, list) else []
            return [self._encode(v, olds[i] if i < len(olds) else None) for i, v in enumerate(node)]
        return node

    def _decode(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: self.crypto.open_text(v)
                if k in ENCRYPTED_FIELDS and is_sealed(v)
                else self._decode(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [self._decode(v) for v in node]
        return node


class AgeBlobCodec:
    suffix = ".age"

    def __init__(self, crypto: Crypto) -> None:
        self.crypto = crypto

    def encode(self, data: bytes) -> bytes:
        return self.crypto.seal(data)

    def decode(self, data: bytes) -> bytes:
        return self.crypto.open(data)
