"""Generated forms laid out like real ones (two columns of boxed captions, right-aligned
values below each caption). Every value is fictional."""

from __future__ import annotations

import pymupdf


def form(cells: list[tuple[float, float, float, str, str, str]], header: list[str] = ()) -> bytes:
    """cells: (x, y, width, box number, caption, value); the value is right-aligned in the box."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for i, line in enumerate(header):
        page.insert_text((40, 40 + 14 * i), line, fontsize=10)
    for x, y, w, number, caption, value in cells:
        page.draw_rect(pymupdf.Rect(x, y, x + w, y + 30), width=0.5)
        page.insert_text((x + 3, y + 9), number, fontsize=7)
        page.insert_text((x + 14, y + 9), caption, fontsize=7)
        if value:
            width = pymupdf.get_text_length(value, fontname="cour", fontsize=10)
            page.insert_text((x + w - 6 - width, y + 24), value, fontname="cour", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def k1(box1: str = "0.", box2: str = "-12,345.", box5: str = "", box19: str = "") -> bytes:
    """Page 1 of a Schedule K-1 (1065) as tax software prints it."""
    left, right = 330, 470
    return form(
        [
            (left, 100, 140, "1", "Ordinary business income (loss)", box1),
            (right, 100, 130, "14", "Self-employment earnings (loss)", "A 0."),
            (left, 130, 140, "2", "Net rental real estate income (loss)", box2),
            (right, 130, 130, "15", "Credits", ""),
            (left, 160, 140, "3", "Other net rental income (loss)", ""),
            (right, 160, 130, "16", "Schedule K-3 is attached", ""),
            (left, 190, 140, "5", "Interest income", box5),
            (right, 190, 130, "19", "Distributions", box19),
            (left, 220, 140, "6a", "Ordinary dividends", ""),
        ],
        [
            "Schedule K-1 (Form 1065) 2025",
            "Partner's Share of Income, Deductions,",
            "A Partnership's employer identification number",
            "00-3456789",
        ],
    )


def k1_fillable(box2: str = "-12,345") -> bytes:
    """The same K-1 with box 2's value in a form field (an IRS fillable PDF)."""
    doc = pymupdf.open(stream=k1(box2=""), filetype="pdf")
    page = doc[0]
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "f1_15[0]"
    w.rect = pymupdf.Rect(400, 148, 466, 160)
    w.field_value = box2
    w.text_fontsize = 9
    page.add_widget(w)
    data = doc.tobytes()
    doc.close()
    return data
