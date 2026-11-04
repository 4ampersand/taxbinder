"""Text layer for classification (§6.2). Held in memory only: never stored or logged,
because tax documents carry SSNs and account numbers. OCR for scans comes later."""

from __future__ import annotations

import io
import logging

import pdfplumber

log = logging.getLogger(__name__)


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "image/heic"  # an iPhone photo
    head = data[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return "text/html"  # e.g. a portal's login page saved instead of the document
    return "application/octet-stream"


def pdf_info(data: bytes, max_pages: int = 3) -> tuple[int | None, str]:
    """(page count, text of the first pages); (None, "") if the PDF can't be read."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:max_pages])
            return len(pdf.pages), text
    except Exception:  # noqa: BLE001 - malformed PDFs are common; classify from the filename
        log.warning("could not read PDF text layer")
        return None, ""
