"""Box values read from forms by label and position (fictional values)."""

from __future__ import annotations

from decimal import Decimal

from kformgen import form, k1

from taxbinder.extract.figures import amount, masked_layout, read_boxes


def test_amounts_as_printed() -> None:
    assert amount("-12,345.") == Decimal("-12345")
    assert amount("0.") == Decimal("0")
    assert amount("(12,345)") == Decimal("-12345")
    assert amount("$1,234.50") == Decimal("1234.50")
    assert amount("12,345-") == Decimal("-12345")
    assert amount("A") is None


def test_k1_boxes_by_position() -> None:
    got = read_boxes(k1(), "k1_1065", ["1", "2", "3", "14"])
    assert got["1"].value == 0 and got["2"].value == Decimal("-12345") and got["2"].page == 1
    assert "3" not in got  # empty box: nothing read, not the value of the box below
    assert "14" in got and got["14"].value == 0  # "A 0." → code A, value 0
    more = read_boxes(k1(box5="1,204.", box19="(25,000)"), "k1_1065", ["5", "19"])
    assert more["5"].value == Decimal("1204") and more["19"].value == Decimal("-25000")


def test_other_forms_and_unknown_boxes() -> None:
    f1098 = form(
        [
            (
                300,
                100,
                200,
                "1",
                "Mortgage interest received from payer(s)/borrower(s)",
                "$ 21,430.10",
            )
        ]
    )
    assert read_boxes(f1098, "1098", ["1"])["1"].value == Decimal("21430.10")
    assert read_boxes(k1(), "k1_1065", ["20 Z"]) == {}  # coded boxes: typed by the person
    assert read_boxes(k1(), "w2", ["1"]) == {}


def test_masked_layout_hides_every_digit() -> None:
    text = masked_layout(k1())
    assert "12,345" not in text and "3456789" not in text and "99,999" in text
    assert "Net rental real estate income" in text


def test_fillable_form_values_are_read() -> None:
    from kformgen import k1_fillable

    assert read_boxes(k1_fillable(), "k1_1065", ["2"])["2"].value == Decimal("-12345")
    assert "f1_15[0] = -99,999" in masked_layout(k1_fillable())


def test_long_packages_are_quick() -> None:
    """A K-1 package with many statement pages after the form (a real 80-page one took 19 s)."""
    import time

    import pymupdf

    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open(stream=k1(), filetype="pdf"))
    for _ in range(60):
        page = doc.new_page()
        page.insert_textbox(
            pymupdf.Rect(40, 40, 570, 750), "Statement of allocations. " * 300, fontsize=8
        )
    data = doc.tobytes()
    start = time.monotonic()
    assert read_boxes(data, "k1_1065", ["2"])["2"].value == Decimal("-12345")
    assert time.monotonic() - start < 2


def test_a_neighbouring_box_number_is_never_a_value() -> None:
    """Box 2 printed with its value elsewhere; the '12' of the box below must not be taken."""
    page = form(
        [
            (330, 100, 140, "2", "Net rental real estate income (loss)", ""),
            (330, 118, 140, "12", "Section 179 deduction", ""),
        ]
    )
    assert read_boxes(page, "k1_1065", ["2"]) == {}


def test_a_form_with_annotations_on_several_pages_is_flattened() -> None:
    """pdfium needs forms enabled before any page is opened (a real K-1 package failed here)."""
    import pymupdf
    from kformgen import k1_fillable

    doc = pymupdf.open(stream=k1_fillable(), filetype="pdf")
    page = doc.new_page()
    page.add_text_annot((100, 100), "a note on page 2")
    data = doc.tobytes()
    assert read_boxes(data, "k1_1065", ["2"])["2"].value == Decimal("-12345")


def test_annotations_without_a_form_are_flattened() -> None:
    """Comments or stamps on a PDF with no fillable form (a real K-1 package had them)."""
    import pymupdf

    doc = pymupdf.open(stream=k1(), filetype="pdf")
    doc[0].add_text_annot((50, 700), "reviewed")
    assert read_boxes(doc.tobytes(), "k1_1065", ["2"])["2"].value == Decimal("-12345")
