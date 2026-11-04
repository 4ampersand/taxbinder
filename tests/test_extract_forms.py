"""The main-page reader: forms found by their title
and OMB number, boxes by label and place, the forms' own checks. Fictional values throughout."""

from __future__ import annotations

from decimal import Decimal

import pymupdf
from kformgen import form

from taxbinder.extract import forms
from taxbinder.extract.figures import read_boxes

K1_HEAD = ["Schedule K-1 (Form 1065) 2025", "OMB No. 1545-0123", "Partner's Share of Income"]
W2_HEAD = ["Form W-2 Wage and Tax Statement 2025", "OMB No. 1545-0008"]


def _k1(box1: str = "1,500.", box19: str = "A 25,000.") -> bytes:
    left, right = 330, 470
    return form(
        [
            (left, 100, 140, "1", "Ordinary business income (loss)", box1),
            (right, 100, 130, "14", "Self-employment earnings (loss)", ""),
            (left, 130, 140, "2", "Net rental real estate income (loss)", "(2,000.)"),
            (right, 130, 130, "15", "Credits", ""),
            (left, 160, 140, "3", "Other net rental income (loss)", ""),
            (right, 160, 130, "19", "Distributions", box19),
            (left, 190, 140, "5", "Interest income", "12."),
        ],
        K1_HEAD,
    )


def _w2(box4: str = "6,200.00") -> bytes:
    left, right = 330, 470
    return form(
        [
            (left, 100, 140, "1", "Wages, tips, other compensation", "100,000.00"),
            (right, 100, 130, "2", "Federal income tax withheld", "15,000.00"),
            (left, 130, 140, "3", "Social security wages", "100,000.00"),
            (right, 130, 130, "4", "Social security tax withheld", box4),
            (left, 160, 140, "5", "Medicare wages and tips", "100,000.00"),
            (right, 160, 130, "6", "Medicare tax withheld", "1,450.00"),
        ],
        W2_HEAD,
    )


def test_a_form_is_found_by_its_title_and_omb_number() -> None:
    got = forms.read(_k1())
    assert list(got) == ["K1_1065"] and got["K1_1065"]["core_pages"] == [1]
    boxes = got["K1_1065"]["boxes"]
    assert boxes["1"]["value"] == 1500 and boxes["2"]["value"] == -2000
    assert boxes["3"]["status"] == "blank"  # an empty box stays empty, never the one below
    assert boxes["19"]["value"] == [{"code": "A", "amount": 25000}]
    assert boxes["6a"]["status"] == "missing"  # not printed on this page
    assert forms.read(form([(330, 100, 140, "1", "Ordinary business income", "5.")])) == {}


def test_the_forms_own_checks() -> None:
    assert forms.read(_w2())["W2"]["boxes"]["4"]["status"] == "ok"
    wrong = forms.read(_w2("9,999.00"))["W2"]["boxes"]
    assert wrong["4"]["status"] == wrong["3"]["status"] == "check-failed"
    assert wrong["4"]["check"] == "4 = 6.2% of 3"
    over = forms.read(_w2(), year=2025)["W2"]["boxes"]  # under the wage base: fine
    assert over["3"]["status"] == "ok"


def test_statement_lines_and_instruction_pages() -> None:
    """A broker's statement ("label ..... amount" on one line) after an instruction page that
    names the same boxes: only the statement is read."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    lines = ["Instructions for Recipient", "Form 1099-INT", "Box 1. Interest income.",
             "Box 4. Federal income tax withheld.", "Box 8. Tax-exempt interest.",
             "Box 3. Interest on U.S. Savings Bonds.", "Box 2. Early withdrawal penalty."]  # fmt: skip
    for i, line in enumerate(lines):
        page.insert_text((40, 60 + 14 * i), line, fontsize=9)
    page = doc.new_page(width=612, height=792)
    page.insert_text((40, 50), "2025 Form 1099-INT Interest Income   OMB No. 1545-0112", fontsize=10)
    for i, (label, value) in enumerate(
        [("1 Interest income", "1,234.56"), ("2 Early withdrawal penalty", "0.00"),
         ("3 Interest on U.S. Savings Bonds", "50.00"), ("4 Federal income tax withheld", "0.00"),
         ("8 Tax-exempt interest", "")]  # fmt: skip
    ):
        page.insert_text((40, 100 + 16 * i), label + " " + "." * 30, fontsize=9)
        if value:
            page.insert_text((400, 100 + 16 * i), value, fontsize=9)
    data = doc.tobytes()
    got = forms.read(data)["1099_INT"]
    assert got["core_pages"] == [2]
    assert got["boxes"]["1"] == {"value": 1234.56, "page": 2, "status": "ok"}
    assert got["boxes"]["3"]["value"] == 50


def test_a_1099b_summary_table() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((40, 50), "2025 Form 1099-B Summary of proceeds  OMB No. 1545-0715", fontsize=10)
    for x, head in [(250, "Proceeds"), (330, "Cost basis"), (410, "Wash sale"), (490, "Gain/loss")]:
        page.insert_text((x, 100), head, fontsize=8)
    rows = [("Short-term covered (Box A)", ["10,000.00", "8,000.00", "0.00", "2,000.00"]),
            ("Long-term covered (Box D)", ["5,000.00", "6,000.00", "0.00", "(1,000.00)"])]  # fmt: skip
    for i, (label, values) in enumerate(rows):
        page.insert_text((40, 120 + 16 * i), label, fontsize=8)
        for x, v in zip((250, 330, 410, 490), values, strict=True):
            page.insert_text((x, 120 + 16 * i), v, fontsize=8)
    boxes = forms.read(doc.tobytes())["1099_B"]["boxes"]
    assert boxes["A_proceeds"]["value"] == 10000 and boxes["A_gain"]["value"] == 2000
    assert boxes["D_gain"]["value"] == -1000 and boxes["D_gain"]["status"] == "ok"


def test_the_app_asks_it_first() -> None:
    """Across the years' boxes: the main-page reader's values, a coded box added up, a blank box
    left blank (not read again by caption), a failed check carried along."""
    got = read_boxes(_k1(), "k1_1065", ["1", "3", "19"])
    assert got["1"].value == Decimal("1500") and got["19"].value == Decimal("25000")
    assert "3" not in got
    w2 = read_boxes(_w2("9,999.00"), "w2", ["1", "4"])
    assert w2["1"].value == Decimal("100000") and w2["1"].check == ""
    assert w2["4"].value == Decimal("9999") and w2["4"].check == "4 = 6.2% of 3"
    # a form it doesn't know is read by caption
    nec = form([(330, 100, 140, "1", "Nonemployee compensation", "3,000.00")], ["Form 1099-NEC"])
    assert read_boxes(nec, "1099_nec", ["1"])["1"].value == Decimal("3000")


def test_values_are_never_logged(caplog) -> None:  # noqa: ANN001
    forms.read(_w2())
    assert "100,000" not in caplog.text and "100000" not in caplog.text


def test_a_wide_box_reaches_past_the_row_under_it() -> None:
    """A 1098's box 1 is as wide as boxes 2 and 3 under it; a lender may print its amount right of
    the label, above box 3's label (seen on a scan)."""
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text in [
        (40, 40, "Form 1098 Mortgage Interest Statement 2025"),
        (40, 54, "OMB No. 1545-1380"),
        (340, 170, "1 Mortgage interest received"),
        (450, 182, "$ 4,321.00"),
        (340, 194, "2 Outstanding mortgage"),
        (425, 194, "3 Mortgage origination date"),
        (340, 208, "$ 123,456.00"),
        (340, 224, "4 Refund of overpaid interest"),
        (420, 224, "5 Mortgage insurance premiums"),
    ]:
        page.insert_text((x, y), text, fontsize=6)
    boxes = forms.read(doc.tobytes())["1098"]["boxes"]
    assert boxes["1"]["value"] == 4321 and boxes["2"]["value"] == 123456
