"""Rule-based classification (§6.2)."""

from __future__ import annotations

import pytest

from taxbinder.ingest.classify import classify
from taxbinder.models import Institution

INSTITUTIONS = [
    Institution(id="tidemark", name="Tidemark", aliases=["TideMark Securities LLC"]),
    Institution(id="brackley-wealth", name="Brackley Wealth"),
    Institution(id="fernhollow-properties", name="Fernhollow properties"),
]


@pytest.mark.parametrize(
    ("text", "filename", "doc_type"),
    [
        ("Schedule K-1 (Form 1065) 2025", "x.pdf", "k1_1065"),
        ("Schedule K-1 (Form 1120-S) 2025", "x.pdf", "k1_1120s"),
        ("FTB Form 592-B 2025", "x.pdf", "k1_state"),
        ("2025 Form 1099-B Proceeds", "x.pdf", "1099_composite"),
        ("Form 1099-INT Interest Income", "x.pdf", "1099_int"),
        ("Form 5498-SA HSA", "x.pdf", "5498_sa"),
        ("Form 5498 IRA Contribution Information", "x.pdf", "5498"),
        ("Form 5498-ESA Coverdell", "x.pdf", None),
        ("Form 1099-R Distributions From Pensions, Annuities, IRAs", "x.pdf", "1099_r"),
        ("Form 1098 Mortgage Interest Statement", "x.pdf", "1098"),
        ("Form W-2 Wage and Tax Statement", "x.pdf", "w2"),
        (
            "Franchise Tax Board MyFTB Web Pay\nPayment Date 09/25/2026\nTax Year 2026",
            "MyFTBPayment.pdf",
            "payment_confirmation",
        ),
        ("Franchise Tax Board notice: you can pay online with Web Pay", "notice.pdf", None),
        (
            "Thank you for your generous gift. No goods or services were provided.",
            "x.pdf",
            "donation_ack",
        ),
        ("", "2025_K1_Kettle410.pdf", "k1_1065"),
        ("nothing useful", "scan.pdf", None),
    ],
)
def test_doc_types(text: str, filename: str, doc_type: str | None) -> None:
    assert classify(text, filename, INSTITUTIONS, 2026).doc_type == doc_type


def test_year_issuer_account_and_corrected() -> None:
    c = classify(
        "CORRECTED\nTideMark Securities LLC\n2025 Form 1099-B\nAccount No. XXXX-2291\nPrinted 2026",
        "statement.pdf",
        INSTITUTIONS,
        2026,
    )
    assert (c.doc_type, c.tax_year, c.issuer) == ("1099_composite", 2025, "tidemark")
    assert c.account_number == "XXXX-2291" and c.is_corrected
    assert c.confidence == 1.0 and c.method == "rules"


def test_future_years_ignored_and_filename_fallback() -> None:
    c = classify("", "Fernhollow properties 2025 K1.pdf", INSTITUTIONS, 2026)
    assert (c.tax_year, c.issuer, c.method) == (2025, "fernhollow-properties", "filename")
    assert classify("statement for 2031", "x.pdf", INSTITUTIONS, 2026).tax_year is None


def test_name_helpers() -> None:
    from taxbinder.ingest.names import filename_words, name_keys, strip_boilerplate

    assert filename_words("K1 2025_MRAster.pdf") == "K1 2025 MR Aster pdf"
    assert filename_words("CFC_AshcombeX_Fund-24-B.pdf") == "CFC Ashcombe X Fund 24 B pdf"
    assert name_keys("Hawksmoor Office Park(HOP)") == ["hop", "hawksmoor"]
    assert name_keys("Aster Apartments(Easton)") == ["aster"]  # places aren't abbreviations
    assert name_keys("Opportunity Fund I") == []
    assert name_keys("Copperfield Collective (CFC)") == ["cfc", "copperfield"]
    assert (
        "irs" not in strip_boilerplate("see IRS Sch. K-3 instructions www.irs.gov/Form1065").lower()
    )


def test_the_earliest_form_title_wins() -> None:
    """A brokerage 1099 that mentions K-1s in its notes is a 1099, not a K-1."""
    text = (
        "2024 Consolidated Tax Statement  Form 1099-DIV ... Form 1099-B ...\n"
        "Supplemental information: income from partnerships is reported on Schedule K-1."
    )
    assert classify(text, "statement.pdf", [], 2026, []).doc_type == "1099_composite"
    k1 = "Schedule K-1 (Form 1065) 2024 ... see the Form 1099-B you received from your broker"
    assert classify(k1, "k1.pdf", [], 2026, []).doc_type == "k1_1065"
    merrow = "2024 TAX REPORTING STATEMENT  Dividends and Distributions"
    assert classify(merrow, "ml.pdf", [], 2026, []).doc_type == "1099_composite"


def test_an_added_type_competes_by_position() -> None:
    """The person's '1099-DIV' type (words: 'Form 1099-DIV') must not take consolidated 1099s,
    whose body lists Form 1099-DIV after their own title; a standalone 1099-DIV still is one."""
    from taxbinder.models import DocTypeDef

    div = DocTypeDef(id="x-1099-div", name="1099-DIV", folder="02-1099", keywords=["Form 1099-DIV"])
    consolidated = (
        "2023 Consolidated 1099 Tax Statement  ...  Form 1099-DIV Dividends and Distributions"
    )
    assert classify(consolidated, "tidemark.pdf", [], 2026, [div]).doc_type == "1099_composite"
    standalone = "Form 1099-DIV  Dividends and Distributions  2023  Copy B for Recipient"
    assert classify(standalone, "savings.pdf", [], 2026, [div]).doc_type == "x-1099-div"
