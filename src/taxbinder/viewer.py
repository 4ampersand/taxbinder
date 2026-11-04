"""The document viewer's pages: each page drawn on this Mac as a PNG picture.

A stored file is never shown as itself inside the app (a saved web page, or a PDF's own scripts,
could run as part of it); a picture of each page can't run anything. PDFs are drawn by
pypdfium2, photos are turned upright and re-encoded; anything else isn't shown."""

from __future__ import annotations

import io

PHOTOS = ("image/jpeg", "image/png", "image/heic")
SCALE = 2.0  # a PDF page at 144 dots per inch: sharp at the viewer's usual sizes
MAX_SIDE = 2400  # a photo is made no larger than this, in pixels


def shown(mime: str) -> bool:
    return mime == "application/pdf" or mime in PHOTOS


def sizes(data: bytes, mime: str) -> list[tuple[float, float]]:
    """Each page's width and height (in any unit: the viewer keeps the shape); [] for a file it
    doesn't show or can't open."""
    if mime in PHOTOS:
        image = _photo(data)
        return [image.size] if image is not None else []
    if mime != "application/pdf":
        return []
    import pypdfium2 as pdfium

    from taxbinder.pdfdoc import PDFIUM

    with PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            return []
        try:
            return [pdf.get_page_size(i) for i in range(len(pdf))]
        finally:
            pdf.close()


def page(data: bytes, mime: str, number: int) -> bytes | None:
    """Page `number` (from 1) as a PNG; None if there's no such page."""
    if mime in PHOTOS:
        image = _photo(data) if number == 1 else None
    elif mime == "application/pdf":
        image = _pdf_page(data, number)
    else:
        image = None
    if image is None:
        return None
    out = io.BytesIO()
    image.save(out, "PNG", optimize=False)
    return out.getvalue()


def _pdf_page(data: bytes, number: int):  # noqa: ANN202 - a PIL image
    import pypdfium2 as pdfium

    from taxbinder.pdfdoc import PDFIUM

    with PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            return None
        try:
            if not 1 <= number <= len(pdf):
                return None
            p = pdf[number - 1]
            try:
                return p.render(scale=SCALE).to_pil()
            finally:
                p.close()
        finally:
            pdf.close()


def _photo(data: bytes):  # noqa: ANN202 - a PIL image
    from PIL import Image, ImageOps

    try:
        import pi_heif

        pi_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        return image.convert("RGB")
    except Exception:  # noqa: BLE001 - not an image this can open
        return None
