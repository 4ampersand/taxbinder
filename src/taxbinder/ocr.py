"""Scans and photos read on the computer: PaddleOCR's PP-OCRv6 models (Apache-2.0)
run by ONNX Runtime (MIT), the same on Mac and Windows; no other image library than Pillow.

A scanned page or a photo becomes the same `pdfdoc.Page` (words with their positions) as a text
PDF's page, so the readers don't need to know a page was scanned. Two steps: the detection model
marks where text is (a probability per pixel; neighbouring marked pixels make one line), then
the recognition model reads each line and says where each letter is, so every word gets its own
box. What's recognised stays in memory, and in the vault only encrypted (`cache` / `cached`);
it's never logged. Nothing leaves the computer, and nothing is downloaded: the models are part
of the app (`ocr_models/`, put there by the build; in development the same files come with the
`rapidocr` package).
"""

from __future__ import annotations

import io
import json
import logging
import math
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from taxbinder.pdfdoc import Box, Page, Word

log = logging.getLogger(__name__)

DPI = 250  # scanned PDF pages are rendered at this resolution (best on IRS forms)
MAX_PAGES = 3  # like text PDFs: the first pages tell what a document is
LETTER_WIDTH = 612.0  # a photo is scaled to a letter page's width, in points
TEXT_LAYER = 20  # a page with fewer characters of text than this is read as a scan
MODELS = ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx")
DET_MIN, DET_MAX = 736, 2400  # the detection input's shorter side at least, longer side at most
THRESH, BOX_THRESH, UNCLIP = 0.3, 0.5, 1.6  # PaddleOCR's settings for its DB detector
LINE_HEIGHT = 48  # the recognition model reads lines this many pixels high
TEXT_SCORE = 0.5  # a line read with less confidence than this is left out
MAX_TILT = 0.3  # degrees: text lines tilted more than this are straightened before reading
_lock = threading.Lock()  # one page at a time: the models use every core already
_engine: _Engine | None = None


def models_dir() -> Path | None:
    """Where the two model files are: inside the app, else (development) in `rapidocr`."""
    here = Path(__file__).parent / "ocr_models"
    if all((here / m).is_file() for m in MODELS):
        return here
    try:
        import rapidocr
    except ImportError:
        return None
    there = Path(rapidocr.__file__).parent / "models"
    return there if all((there / m).is_file() for m in MODELS) else None


def available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return models_dir() is not None


def warm_up() -> None:
    """Load the models in the background (a second or two), so the first upload doesn't wait."""
    if available():
        threading.Thread(target=_warm, name="ocr-warm-up", daemon=True).start()


def _warm() -> None:
    try:
        with _lock:
            _get()
    except Exception:  # noqa: BLE001 - only a head start; reading works without it
        log.warning("text recognition didn't warm up")


def _get() -> _Engine:
    global _engine
    if _engine is None:
        _engine = _Engine(models_dir())  # type: ignore[arg-type]
    return _engine


class _Engine:
    def __init__(self, folder: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3  # errors only: nothing about the pages is logged
        cpu = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(str(folder / MODELS[0]), options, providers=cpu)
        self.rec = ort.InferenceSession(str(folder / MODELS[1]), options, providers=cpu)
        letters = self.rec.get_modelmeta().custom_metadata_map["character"].splitlines()
        self.letters = ["", *letters, " "]  # 0 is CTC's blank; the space comes last

    def lines(self, rgb: Any) -> tuple[Any, list[tuple[tuple[float, float, float, float], list]]]:  # noqa: ANN401
        """(the image as read, straightened if it was tilted; each text line's box in pixels and
        its words [(text, (x0, x1))], top to bottom)."""
        boxes, tilt = self._detect(rgb)
        if abs(tilt) > MAX_TILT:  # a photo or a scan fed in at an angle: straightened first
            import numpy as np
            from PIL import Image

            image = Image.fromarray(rgb).rotate(tilt, Image.BICUBIC, fillcolor=(255, 255, 255))
            rgb = np.asarray(image)
            boxes, _ = self._detect(rgb)
        found = [(b, words) for b, words in zip(boxes, self._recognise(rgb, boxes), strict=True)
                 if words]  # fmt: skip
        return rgb, found

    def _detect(self, rgb: Any) -> tuple[list[tuple[float, float, float, float]], float]:  # noqa: ANN401
        """The text lines' boxes (pixels), and how far they slope down to the right (degrees)."""
        import numpy as np
        from PIL import Image

        h, w = rgb.shape[:2]
        ratio = min(1.0, DET_MAX / max(h, w))
        if min(h, w) * ratio < DET_MIN:
            ratio = min(DET_MIN / min(h, w), DET_MAX / max(h, w))
        rh, rw = (max(32, round(h * ratio / 32) * 32), max(32, round(w * ratio / 32) * 32))
        img = np.asarray(Image.fromarray(rgb).resize((rw, rh), Image.BILINEAR), dtype=np.float32)
        x = ((img / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
        prob = self.det.run(None, {self.det.get_inputs()[0].name: x})[0][0, 0]
        mask = prob > THRESH
        grown = mask.copy()  # dilated by 2×2, as PaddleOCR does
        grown[1:, :] |= mask[:-1, :]
        grown[:, 1:] |= mask[:, :-1]
        grown[1:, 1:] |= mask[:-1, :-1]
        out, slopes = [], []
        for y0, y1, x0, x1, score, slope in _components(grown, prob):
            bw, bh = x1 - x0, y1 - y0
            if min(bw, bh) < 3 or score < BOX_THRESH:
                continue
            if slope is not None:
                slopes.append(slope)
            d = bw * bh * UNCLIP / (2 * (bw + bh))  # the detector marks a line's core: grow it
            bx0, by0 = max(0.0, (x0 - d) * w / rw), max(0.0, (y0 - d) * h / rh)
            bx1, by1 = min(float(w), (x1 + d) * w / rw), min(float(h), (y1 + d) * h / rh)
            if bx1 - bx0 > 3 and by1 - by0 > 3:
                out.append((bx0, by0, bx1, by1))
        tilt = math.degrees(math.atan(float(np.median(slopes)))) if len(slopes) >= 5 else 0.0
        return sorted(out, key=lambda b: (round(b[1] / 10), b[0])), tilt

    def _recognise(self, rgb: Any, boxes: list) -> list[list]:  # noqa: ANN401
        import numpy as np
        from PIL import Image

        crops = []
        for x0, y0, x1, y1 in boxes:
            crop = Image.fromarray(rgb[int(y0) : math.ceil(y1), int(x0) : math.ceil(x1)])
            width = max(1, math.ceil(LINE_HEIGHT * crop.width / max(crop.height, 1)))
            crops.append(np.asarray(crop.resize((width, LINE_HEIGHT), Image.BICUBIC)))
        order = sorted(range(len(crops)), key=lambda i: crops[i].shape[1])
        out: list[list] = [[] for _ in crops]
        for start in range(0, len(order), 6):  # six lines at a time, of similar widths
            batch = order[start : start + 6]
            width = max(320, max(crops[i].shape[1] for i in batch))
            x = np.zeros((len(batch), 3, LINE_HEIGHT, width), dtype=np.float32)
            for j, i in enumerate(batch):
                c = crops[i].astype(np.float32).transpose(2, 0, 1) / 255.0
                x[j, :, :, : c.shape[2]] = (c - 0.5) / 0.5
            preds = self.rec.run(None, {self.rec.get_inputs()[0].name: x})[0]
            for j, i in enumerate(batch):
                step = width / preds.shape[1]  # input pixels per output column
                scale = (boxes[i][2] - boxes[i][0]) / crops[i].shape[1]
                out[i] = self._words(preds[j], step * scale, boxes[i][0])
        return out

    def _words(self, pred: Any, column: float, left: float) -> list:  # noqa: ANN401
        """CTC greedy decoding: [(word, (x0, x1))] in pixels, or [] if read too unsurely."""
        best, prob = pred.argmax(axis=1), pred.max(axis=1)
        keep = [c for c in range(len(best)) if best[c] != 0 and (c == 0 or best[c] != best[c - 1])]
        if not keep or float(prob[keep].mean()) < TEXT_SCORE:
            return []
        words, current = [], []
        for c in keep:
            ch = self.letters[best[c]] if best[c] < len(self.letters) else ""
            if ch == " " or not ch:
                if current:
                    words.append(current)
                current = []
            else:
                current.append((ch, c))
        if current:
            words.append(current)
        return [
            ("".join(ch for ch, _ in w), (left + w[0][1] * column, left + (w[-1][1] + 1) * column))
            for w in words
        ]


def _components(mask: Any, prob: Any) -> list[tuple[int, int, int, int, float, float | None]]:  # noqa: ANN401
    """Connected groups of marked pixels (8 neighbours), from the runs on each row: (top,
    bottom, left, right, mean probability, slope of a long line or None)."""
    import numpy as np

    padded = np.pad(mask, ((0, 0), (1, 1))).astype(np.int8)
    d = np.diff(padded, axis=1)
    rows, starts = np.nonzero(d == 1)
    _, ends = np.nonzero(d == -1)  # the same order: one end per start, row by row
    n = len(rows)
    if n == 0:
        return []
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = np.searchsorted(rows, np.arange(rows.max() + 2))  # each row's first run
    for r in range(1, int(rows.max()) + 1):
        a, b = first[r - 1], first[r]  # runs on the row above
        c, e = first[r], first[r + 1]  # runs on this row
        i, j = a, c
        while i < b and j < e:
            if starts[j] <= ends[i] and ends[j] >= starts[i]:  # touching, corners included
                ri, rj = root(i), root(j)
                if ri != rj:
                    parent[rj] = ri
            if ends[i] < ends[j]:
                i += 1
            else:
                j += 1
    label = np.array([root(i) for i in range(n)])
    cs = np.pad(np.cumsum(prob, axis=1), ((0, 0), (1, 0)))
    sums = cs[rows, ends] - cs[rows, starts]
    out = []
    for lab in np.unique(label):
        k = label == lab
        lengths = (ends[k] - starts[k]).astype(float)
        top, bottom = int(rows[k].min()), int(rows[k].max()) + 1
        left, right = int(starts[k].min()), int(ends[k].max())
        slope = None
        if right - left > 8 * (bottom - top) and right - left > 40:  # a long line of text
            # the line through its pixels (least squares of row on column, over every pixel)
            s, e, y = starts[k].astype(float), ends[k].astype(float) - 1, rows[k].astype(float)
            n = lengths.sum()
            sx = (lengths * (s + e) / 2).sum()
            sxx = ((e * (e + 1) * (2 * e + 1) - (s - 1) * s * (2 * s - 1)) / 6).sum()
            sy, sxy = (y * lengths).sum(), (y * lengths * (s + e) / 2).sum()
            var = sxx / n - (sx / n) ** 2
            if var > 0:
                slope = float((sxy / n - (sx / n) * (sy / n)) / var)
        out.append((top, bottom, left, right, float(sums[k].sum()) / lengths.sum(), slope))
    return out


def _recognise(image: Any, width: float, height: float, number: int) -> Page:  # noqa: ANN401
    """Words and positions on one image (a Pillow image), as a page `width` × `height` points."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB"))
    with _lock:
        rgb, found = _get().lines(rgb)
    sx, sy = width / rgb.shape[1], height / rgb.shape[0]
    words, texts = [], []
    for i, ((_, y0, _, y1), ws) in enumerate(found):
        for text, (x0, x1) in ws:
            words.append(Word(text, Box(x0 * sx, y0 * sy, x1 * sx, y1 * sy), i))
        texts.append(" ".join(t for t, _ in ws))
    return Page(number, width, height, "\n".join(texts), words, 0, 1)


def read_photo(data: bytes) -> list[Page]:
    """A photo (JPEG, PNG, HEIC) as one upright page; [] if it can't be read."""
    if not available():
        return []
    from PIL import Image, ImageOps

    try:
        import pi_heif

        pi_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # upright, as it was taken
    except Exception:  # noqa: BLE001 - not an image this can open
        return []
    w, h = image.size
    return [_recognise(image, LETTER_WIDTH, LETTER_WIDTH * h / w, 1)]


def read_pdf(data: bytes) -> list[Page]:
    """The scanned pages among a PDF's first pages (a page with a text layer is left out)."""
    if not available():
        return []
    import pypdfium2 as pdfium

    from taxbinder.pdfdoc import PDFIUM

    scans = []  # drawn holding PDFium's lock, recognised after it (that takes a while)
    with PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            return []
        try:
            for i in range(min(len(pdf), MAX_PAGES)):
                page = pdf[i]
                try:
                    tp = page.get_textpage()
                    if len(tp.get_text_range(0, tp.count_chars()).strip()) >= TEXT_LAYER:
                        continue  # it has text: read as text, never OCR'd
                    width, height = page.get_size()
                    image = page.render(scale=DPI / 72, grayscale=True).to_pil()
                    scans.append((image, width, height, i + 1))
                finally:
                    page.close()
        finally:
            pdf.close()
    return [_recognise(*scan) for scan in scans]


def read(data: bytes, mime: str) -> list[Page]:
    """The pages of a scan or photo that need reading; [] for a text PDF or anything else."""
    try:
        if mime == "application/pdf":
            return read_pdf(data)
        if mime in ("image/jpeg", "image/png", "image/heic"):
            return read_photo(data)
    except Exception:  # noqa: BLE001 - an unreadable image is reported as unread, never a crash
        log.warning("text recognition failed on a document")
    return []


def dumps(pages: list[Page]) -> bytes:
    """Recognised pages as JSON, for the vault's encrypted file store."""
    return json.dumps([asdict(p) for p in pages], separators=(",", ":")).encode()


def loads(data: bytes) -> list[Page]:
    out = []
    for p in json.loads(data):
        words = [Word(w["text"], Box(**w["box"]), w["line"]) for w in p.pop("words")]
        out.append(Page(**p, words=words))
    return out


def text_of(pages: list[Page]) -> str:
    return "\n".join(p.text for p in pages)
