"""Each form read twice and compared: by the rules and by the reading model.

`forms.read` finds each form's main page and reads its boxes by the rules. The model then reads
the same page, as an image with the page's text beside it, for that form's boxes, answering in
a fixed JSON shape. Each box is then marked:

  confirmed     the rules read the same value: trusted
  disagree      the rules read something else (both kept: `value` is the model's, `rule_value`)
  llm           only the model has a value
  check-failed  it breaks one of the form's checks (W-2 4 = 6.2% of 3, …)
  rules-only    the model failed on this form (its answer was cut off, …): the rules' values

and `grounded` is False when the model's number isn't printed anywhere on the page (an invented
value). Only `confirmed` boxes are taken without a look. Values are never logged.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from typing import Any

from taxbinder import slack
from taxbinder.engine import Unanswered
from taxbinder.extract import forms
from taxbinder.institutions import tin_of
from taxbinder.pdfdoc import Page

log = logging.getLogger(__name__)

SYSTEM = (
    "You read US tax forms. Report only what is printed on the form. Use null for an empty box "
    "and 0 for a box that shows 0.00 or NONE. Amounts are plain numbers: no $ sign or commas, "
    "negative when shown in parentheses or with a minus sign. If the page holds several copies "
    "of the same form, read the first complete copy only."
)
SCALE = 2  # the page image: 144 dpi
MAX_TEXT = 12000  # characters of the page's text sent with its image
TICK = "only boxes actually marked with an X, check mark or filled square; empty squares are not ticked"  # noqa: E501
HINTS = {  # wording where the label pattern makes a poor description
    ("W2", "12a"): "box 12a only: the code and amount printed in box 12a (not 12b-12d)",
    ("W2", "12b"): "box 12b only",
    ("W2", "12c"): "box 12c only",
    ("W2", "12d"): "box 12d only",
    ("W2", "13"): f"box 13 checkboxes ({TICK})",
    ("W2", "14"): "box 14 Other: description and amount per line",
    ("W2", "15"): "box 15 state: the 2-letter state code only (not the employer's state ID)",
    ("1099_R", "2b"): f"box 2b checkboxes ({TICK})",
    ("1099_R", "IRA"): f"IRA/SEP/SIMPLE checkbox ({TICK})",
    ("1099_R", "7"): "box 7 distribution code(s), e.g. G or 7 or 4D",
    ("1099_R", "15"): "box 15 state/payer's state no.",
    ("5498_SA", "1"): "box 1 employee or self-employed person's Archer MSA contributions",
    ("5498_SA", "2"): "box 2 total contributions made in the year",
    ("5498_SA", "6"): f"box 6 account type ({TICK})",
}


def describe(pattern: str) -> str:
    """A readable label from a label pattern ("s ?t ?a ?t ?u ?t ?o ?r ?y|stat\\b" → "statutory")."""
    first = pattern.replace(forms.MTG, "mortgage").split("|")[0]
    first = re.sub(r"\(\?!.*?\)|\\b|\\w\*|\(\.s\)\?| \?|[\\^$?()]", "", first)
    return first.strip() or "(see form)"


def box_kinds(name: str, form: dict[str, Any]) -> dict[str, tuple[dict, str]]:
    """Box id → (its JSON schema, its description for the model)."""
    out: dict[str, tuple[dict, str]] = {}
    if "table" in form:  # a 1099-B summary: rows by Form 8949 box letter
        for letter in "ABCDEFX":
            for col in form["table"]:
                label = f"Box {letter} row, {col} column"
                out[f"{letter}_{col}"] = ({"type": ["number", "null"]}, label)
        return out
    for box, pattern in form["boxes"].items():
        label = HINTS.get((name, box), f"box {box} {describe(pattern)}")
        if box in form.get("checkboxes", {}):
            names = list(form["checkboxes"][box])
            schema: dict[str, Any] = {"type": "array", "items": {"type": "string", "enum": names}}
            label += f" — names: {', '.join(names)}"
        elif box in form["coded"]:
            line = {"type": "object", "additionalProperties": False, "required": ["code", "amount"],
                    "properties": {"code": {"type": ["string", "null"]},
                                   "amount": {"type": ["number", "null"]}}}  # fmt: skip
            schema = {"type": "array", "items": line}
            label += " (one entry per printed line: code and amount; amount null if it says STMT)"
        elif box in form.get("dates", ()) or box in form.get("texts", {}):
            schema = {"type": ["string", "null"]}
        else:
            schema = {"type": ["number", "null"]}
        out[box] = (schema, label)
    return out


# Who and when, asked with the boxes: kept apart from them in the answer
PARTIES = {
    "_sender": ("the name of who issued the form, as printed: the payer, employer, partnership, "
                "lender or trustee; never the recipient, an address or a heading"),
    "_tax_year": "the tax year the form is for (4 digits)",
    "_recipient": "the recipient's name as printed (the person or trust the form is for)",
    "_sender_tin": ("the issuer's EIN as printed, like 00-1234567: a K-1's partnership (box A), "
                    "a W-2's employer (box b), a 1099's payer; never the recipient's number"),
}  # fmt: skip


def ask(engine: Any, name: str, png: bytes, text: str) -> dict[str, Any]:  # noqa: ANN401 - an Engine
    """The model's reading of one form's main page: its boxes, and who sent it, for whom, when."""
    form = forms.FORMS[name]
    kinds = box_kinds(name, form)
    kinds |= {k: ({"type": ["integer" if k == "_tax_year" else "string", "null"]}, v)
              for k, v in PARTIES.items()}  # fmt: skip
    schema = {"type": "object", "additionalProperties": False, "required": list(kinds),
              "properties": {b: s for b, (s, _) in kinds.items()}}  # fmt: skip
    boxes = "\n".join(f"- {b}: {label}" for b, (_, label) in kinds.items())
    prompt = (f"This page is tax form {name.replace('_', ' ')}. Read these boxes, and the "
              f"fields starting with _:\n{boxes}")  # fmt: skip
    if text:
        prompt += "\n\nThe page's text, for reference (the image is authoritative):\n"
        prompt += text[:MAX_TEXT]
    image = "data:image/png;base64," + base64.b64encode(png).decode()
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": prompt},
                                     {"type": "image_url", "image_url": {"url": image}}]},
    ]  # fmt: skip
    answer = engine.ask(messages, schema)
    return _floats(json.loads(re.sub(r"^```(json)?|```$", "", answer.strip()).strip()))


def _floats(v: Any) -> Any:  # noqa: ANN401
    """Whole-number answers (10100) as floats, like the rules' values."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, list):
        return [_floats(i) for i in v]
    return {k: _floats(i) for k, i in v.items()}


def same(a: Any, b: Any) -> bool:  # noqa: ANN401
    if isinstance(a, list) or isinstance(b, list):
        # codes compared without symbols ("*A" = "A"); STMT lines (no amount) don't count
        def key(i: Any) -> str:  # noqa: ANN401
            if isinstance(i, dict):
                code = re.sub(r"[^A-Z0-9]", "", str(i.get("code") or "").upper())
                return str((code, i.get("amount")))
            return str(i)

        def keep(v: Any) -> list:  # noqa: ANN401
            return [i for i in v or [] if not (isinstance(i, dict) and i.get("amount") is None)]

        return sorted(map(key, keep(a))) == sorted(map(key, keep(b)))
    if isinstance(a, str) or isinstance(b, str):
        return str(a or "").strip().upper() == str(b or "").strip().upper()
    return (a in (None, 0) and b in (None, 0)) or (
        a is not None and b is not None and abs(a - b) < 0.005
    )


def grounded(value: Any, tokens: set[str], numbers: set[float]) -> bool:  # noqa: ANN401
    """Is the value printed on the page? Numbers among the page's amounts, text among its words."""
    if value in (None, 0, [], ""):
        return True
    if isinstance(value, list):
        return all(
            grounded(i.get("amount"), tokens, numbers) and grounded(i.get("code"), tokens, numbers)
            if isinstance(i, dict)
            else True
            for i in value
        )
    if isinstance(value, str):
        return value.upper() in tokens or any(value.upper() in t for t in tokens)
    return any(abs(abs(value) - n) < 0.005 for n in numbers)


def printed(name: Any, text: str) -> bool:  # noqa: ANN401
    """Is a name the model read printed on the page (each of its words, any case)?"""
    if not isinstance(name, str) or not name.strip():
        return False
    words = set(re.findall(r"[a-z0-9&']+", text.lower()))
    return all(w in words for w in re.findall(r"[a-z0-9&']+", name.lower()))


def tin_printed(tin: Any, text: str) -> bool:  # noqa: ANN401
    """Is an EIN the model read on the page (look-alike letters in a scan's text read as
    digits)?"""
    want = re.sub(r"\D", "", str(tin or ""))
    found = re.findall(r"\d{2}\s*-?\s*\d{7}", slack.digits(text))
    return len(want) == 9 and any(re.sub(r"\D", "", f) == want for f in found)


def page_image(data: bytes | None, number: int, mime: str = "application/pdf") -> bytes:
    """One page as a PNG for the model: a PDF page at 144 dpi, a photo upright and at most
    1,600 pixels wide."""
    from PIL import Image, ImageOps

    out = io.BytesIO()
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        from taxbinder.pdfdoc import PDFIUM

        with PDFIUM:
            pdf = pdfium.PdfDocument(data)
            try:
                page = pdf[number - 1]
                try:
                    image = page.render(scale=SCALE).to_pil()
                finally:
                    page.close()
            finally:
                pdf.close()
        image.save(out, "PNG")
    else:
        try:
            import pi_heif

            pi_heif.register_heif_opener()
        except ImportError:
            pass
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data or b""))).convert("RGB")
        if image.width > 1600:
            image = image.resize((1600, round(image.height * 1600 / image.width)))
        image.save(out, "PNG")
    return out.getvalue()


def read(
    engine: Any,  # noqa: ANN401 - an engine.Engine
    data: bytes | None,
    scans: list[Page] | None = None,
    year: int | None = None,
    mime: str = "application/pdf",
    model: str = "",
) -> dict[str, Any]:
    """{form: {"core_pages", "seconds", "boxes": {box: {"value", "page", "status", "grounded",
    "rule_value"?, "check"?}}}, …} for each known form in the document, and "model"."""
    pdf_data = data if mime == "application/pdf" else None
    rules = forms.read(pdf_data, scans, year)
    words = dict(forms.page_words(pdf_data, scans))
    out: dict[str, Any] = {"model": model, "forms": {}}
    for name, entry in rules.items():
        number = entry["core_pages"][0]
        page = words.get(number, [])
        tokens = {w["text"].upper() for w in page}
        numbers = {abs(forms.to_number(w["text"])) for w in page if forms.AMOUNT.match(w["text"])}
        text = "\n".join(" ".join(w["text"] for w in seg["words"]) for seg in forms.segments(page))
        started = time.monotonic()
        try:
            answer = ask(engine, name, page_image(data, number, mime), text)
        except (Unanswered, ValueError, KeyError, TypeError) as e:  # its answer, not the engine
            log.warning("the model couldn't read a %s (%s)", name, type(e).__name__)
            out["forms"][name] = {
                "core_pages": entry["core_pages"],
                "model_error": type(e).__name__,
                "boxes": {b: {**v, "status": "rules-only", "grounded": True}
                          for b, v in entry["boxes"].items()},  # fmt: skip
            }
            continue
        if "parties" not in out:  # from the first form's page (a composite's parts agree)
            out["parties"] = {
                "sender": {"value": answer.get("_sender"),
                           "printed": printed(answer.get("_sender"), text)},
                "tax_year": {"value": answer.get("_tax_year"),
                             "printed": str(answer.get("_tax_year") or "")[:4] in text},
                "recipient": {"value": answer.get("_recipient"),
                              "printed": printed(answer.get("_recipient"), text)},
                "sender_tin": {"value": tin_of(answer.get("_sender_tin")),
                               "printed": tin_printed(answer.get("_sender_tin"), text)},
            }  # fmt: skip
        answer = {k: v for k, v in answer.items() if not k.startswith("_")}
        boxes: dict[str, dict[str, Any]] = {}
        for box, value in answer.items():
            rule = entry["boxes"].get(box, {}).get("value")
            if rule in (None, []) and value not in (None, [], 0):
                status = "llm"
            else:
                status = "confirmed" if same(value, rule) else "disagree"
            boxes[box] = {"value": value, "page": number, "status": status,
                          "grounded": grounded(value, tokens, numbers)}  # fmt: skip
            if status == "disagree":
                boxes[box]["rule_value"] = rule
        checks = {b: {"value": v["value"], "status": "ok"} for b, v in boxes.items()}
        forms.run_checks(forms.FORMS[name], checks, year)  # the same arithmetic as the rules
        for b, v in checks.items():
            if v["status"] == "check-failed":
                boxes[b]["status"], boxes[b]["check"] = "check-failed", v["check"]
        seconds = round(time.monotonic() - started, 1)
        out["forms"][name] = {"core_pages": entry["core_pages"], "seconds": seconds, "boxes": boxes}
    return out
