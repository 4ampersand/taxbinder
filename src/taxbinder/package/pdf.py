"""Minimal text-only PDF writer for the package cover memo (Helvetica, letter size)."""

from __future__ import annotations

import textwrap

LINES_PER_PAGE = 54
WIDTH = 95  # characters per line at 10 pt


def _escape(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        indent = len(line) - len(line.lstrip())
        out.extend(textwrap.wrap(line, WIDTH, subsequent_indent=" " * (indent + 2)) or [""])
    return out


def text_pdf(lines: list[str]) -> bytes:
    wrapped = _wrap(lines)
    pages = [wrapped[i : i + LINES_PER_PAGE] for i in range(0, len(wrapped), LINES_PER_PAGE)] or [
        []
    ]
    n = len(pages)
    font_id = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    for i, page in enumerate(pages):
        body = " ".join(f"({_escape(line)}) Tj T*" for line in page)
        stream = f"BT /F1 10 Tf 13 TL 54 740 Td {body} ET".encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
