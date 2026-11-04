"""How blobs and record fields are written to disk, as a seam: the vault's encrypting codecs
(`store/crypto.py`, §4.5, §4.6) or the plain ones, which pass data through unchanged, without
the callers knowing which.
"""

from __future__ import annotations

from typing import Any, Protocol


class BlobCodec(Protocol):
    suffix: str  # appended to blob file names, e.g. ".age"

    def encode(self, data: bytes) -> bytes: ...
    def decode(self, data: bytes) -> bytes: ...


class FieldCodec(Protocol):
    def encode(self, data: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        """Record data → on-disk form. `previous` is the current on-disk form, if any."""
        ...

    def decode(self, data: dict[str, Any]) -> dict[str, Any]: ...


class PlainBlobCodec:
    suffix = ""

    def encode(self, data: bytes) -> bytes:
        return data

    def decode(self, data: bytes) -> bytes:
        return data


class PlainFieldCodec:
    def encode(self, data: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        return data

    def decode(self, data: dict[str, Any]) -> dict[str, Any]:
        return data
