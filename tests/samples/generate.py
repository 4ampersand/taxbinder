"""Generate the fictional sample set into a folder: PDFs laid out like
the real forms, scanned and photographed copies, a few bad files, and `expected.yaml` with the
right answer for each. Every value is invented (household.py); nothing generated is committed.

    uv run python -m tests.samples.generate OUT_DIR
"""

from __future__ import annotations

import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pymupdf
from ruamel.yaml import YAML

from .household import ADDRESS, ENTITIES, FILERS, FISCAL, ISSUERS, MISSING_2024, PEOPLE, TRUST

YEARS = (2023, 2024, 2025)
# Types the app doesn't have yet; the steps that add them use these ids.
NEW_TYPES = {"tax_return": "R1"}
PAYER = "PAYER'S name, street address, city or town, state or province, country, ZIP"


@dataclass(frozen=True)
class Spec:
    title: str
    subtitle: str
    issuer: str | None  # caption above the issuer's name; None: the name is a letterhead
    issuer_tin: str
    recipient: str
    recipient_tin: str
    account: str | None
    boxes: tuple[tuple[str, str], ...]  # (box, caption as printed)


SPECS = {
    "w2": Spec(
        "Form W-2",
        "Wage and Tax Statement",
        "c Employer's name, address, and ZIP code",
        "b Employer identification number (EIN)",
        "e Employee's first name and initial   Last name",
        "a Employee's social security number",
        None,
        (
            ("1", "Wages, tips, other compensation"),
            ("2", "Federal income tax withheld"),
            ("3", "Social security wages"),
            ("4", "Social security tax withheld"),
            ("12a", "See instructions for box 12"),
        ),
    ),
    "1099_int": Spec(
        "Form 1099-INT",
        "Interest Income",
        PAYER,
        "PAYER'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number (see instructions)",
        (
            ("1", "Interest income"),
            ("3", "Interest on U.S. Savings Bonds and Treasury obligations"),
            ("4", "Federal income tax withheld"),
            ("8", "Tax-exempt interest"),
        ),
    ),
    "1099_div": Spec(
        "Form 1099-DIV",
        "Dividends and Distributions",
        PAYER,
        "PAYER'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number (see instructions)",
        (
            ("1a", "Total ordinary dividends"),
            ("1b", "Qualified dividends"),
            ("2a", "Total capital gain distr."),
        ),
    ),
    "1099_composite": Spec(
        "Consolidated Form 1099",
        "Tax Reporting Statement",
        None,
        "Payer's federal ID",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number",
        (
            ("1a", "Total ordinary dividends"),
            ("1b", "Qualified dividends"),
            ("1", "Interest income"),
        ),
    ),
    "1099_r": Spec(
        "Form 1099-R",
        "Distributions From Pensions, Annuities, Retirement or Profit-Sharing Plans",
        PAYER,
        "PAYER'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number",
        (
            ("1", "Gross distribution"),
            ("2a", "Taxable amount"),
            ("4", "Federal income tax withheld"),
        ),
    ),
    "5498": Spec(
        "Form 5498",
        "IRA Contribution Information",
        "TRUSTEE'S or ISSUER'S name, street address, city or town, state or province, country",
        "TRUSTEE'S or ISSUER'S TIN",
        "PARTICIPANT'S name",
        "PARTICIPANT'S TIN",
        "Account number (see instructions)",
        (("1", "IRA contributions"), ("5", "Fair market value of account")),
    ),
    "1098": Spec(
        "Form 1098",
        "Mortgage Interest Statement",
        "RECIPIENT'S/LENDER'S name, street address, city or town, state or province, country",
        "RECIPIENT'S/LENDER'S TIN",
        "PAYER'S/BORROWER'S name",
        "PAYER'S/BORROWER'S TIN",
        "Account number (see instructions)",
        (
            ("1", "Mortgage interest received from payer(s)/borrower(s)"),
            ("2", "Outstanding mortgage principal"),
            ("10", "Real estate taxes (paid from escrow)"),
        ),
    ),
    "ssa_1099": Spec(
        "Form SSA-1099",
        "Social Security Benefit Statement",
        None,
        "",
        "Box 1. Name",
        "Box 2. Beneficiary's Social Security Number",
        None,
        (("3", "Benefits paid in {year}"), ("5", "Net benefits for {year}")),
    ),
    "1099_q": Spec(
        "Form 1099-Q",
        "Payments From Qualified Education Programs",
        "PAYER'S/TRUSTEE'S name, street address, city or town, state or province, country",
        "PAYER'S/TRUSTEE'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number",
        (("1", "Gross distribution"), ("2", "Earnings"), ("3", "Basis")),
    ),
    "5498_sa": Spec(
        "Form 5498-SA",
        "HSA, Archer MSA, or Medicare Advantage MSA Information",
        "TRUSTEE'S name, street address, city or town, state or province, country",
        "TRUSTEE'S TIN",
        "PARTICIPANT'S name",
        "PARTICIPANT'S TIN",
        "Account number",
        (("2", "Total contributions made in {year}"),),
    ),
    "1099_nec": Spec(
        "Form 1099-NEC",
        "Nonemployee Compensation",
        PAYER,
        "PAYER'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number (see instructions)",
        (("1", "Nonemployee compensation"), ("4", "Federal income tax withheld")),
    ),
    "1099_misc": Spec(
        "Form 1099-MISC",
        "Miscellaneous Information",
        PAYER,
        "PAYER'S TIN",
        "RECIPIENT'S name",
        "RECIPIENT'S TIN",
        "Account number (see instructions)",
        (("1", "Rents"), ("3", "Other income")),
    ),
    "k1_1065": Spec(
        "Schedule K-1 (Form 1065)",
        "Partner's Share of Income, Deductions, Credits, etc.",
        "B Partnership's name, address, city, state, and ZIP code",
        "A Partnership's employer identification number",
        "F Name, address, city, state, and ZIP code for partner entered in E",
        "E Partner's SSN or TIN",
        None,
        (
            ("1", "Ordinary business income (loss)"),
            ("2", "Net rental real estate income (loss)"),
            ("5", "Interest income"),
            ("19", "Distributions"),
        ),
    ),
    "k1_1120s": Spec(
        "Schedule K-1 (Form 1120-S)",
        "Shareholder's Share of Income, Deductions, Credits, etc.",
        "B Corporation's name, address, city, state, and ZIP code",
        "A Corporation's employer identification number",
        "F Shareholder's name, address, city, state, and ZIP code",
        "E Shareholder's identifying number",
        None,
        (
            ("1", "Ordinary business income (loss)"),
            ("2", "Net rental real estate income (loss)"),
            ("4", "Interest income"),
        ),
    ),
    "k1_1041": Spec(
        "Schedule K-1 (Form 1041)",
        "Beneficiary's Share of Income, Deductions, Credits, etc.",
        "B Estate's or trust's name",
        "A Estate's or trust's employer identification number",
        "F Beneficiary's name, address, city, state, and ZIP code",
        "E Beneficiary's identifying number",
        None,
        (("1", "Interest income"), ("2a", "Ordinary dividends")),
    ),
}
K1 = {"k1_1065", "k1_1120s", "k1_1041"}


@dataclass
class Doc:
    doc_type: str
    year: int  # the tax year it belongs to (the answer)
    issuer: str
    person: str  # who it's addressed to: taxpayer, spouse or trust
    account: str | None = None  # as printed (fictional); the answer is its last 4
    figures: dict[str, Decimal | None] = field(default_factory=dict)  # None: box left blank
    layout: str = "tuned"  # or held_out: a layout the reader isn't tuned on
    corrected: bool = False
    printed_year: int | None = None  # the year on the form when it differs (fiscal year)
    file: str = ""

    @property
    def filer(self) -> str:
        return "trust" if self.person == "trust" else "household"


def _money(rng: random.Random, low: int, high: int) -> Decimal:
    return Decimal(f"{rng.randint(low, high)}.{rng.randint(0, 99):02d}")


def _whole(rng: random.Random, low: int, high: int) -> Decimal:
    return Decimal(rng.randint(low, high))


def plan(rng: random.Random) -> list[Doc]:
    """Every text document in the set, with its answer."""
    docs: list[Doc] = []
    for y in YEARS:
        wages = _money(rng, 120_000, 180_000)
        benefits = Decimal(f"{rng.randint(20_000, 24_000)}.00")
        docs += [
            Doc(
                "w2",
                y,
                "brightwater-analytics",
                "taxpayer",
                figures={
                    "1": wages,
                    "2": _money(rng, 18_000, 30_000),
                    "3": wages,
                    "4": (wages * Decimal("0.062")).quantize(Decimal("0.01")),
                    "12a": Decimal(6000 + 500 * (y - 2023)),  # retirement savings (no rng)
                },
            ),
            Doc(
                "w2",
                y,
                "sorrel-valley-health",
                "spouse",
                figures={"1": _money(rng, 40_000, 70_000), "2": _money(rng, 4_000, 9_000)},
            ),
            Doc(
                "1099_int",
                y,
                "tamarack-savings-bank",
                "taxpayer",
                "0047113304",
                figures={"1": _money(rng, 600, 2_400), "3": None, "4": Decimal("0.00"),
                         "8": Decimal(240 + 30 * (y - 2023))},  # municipal bonds (no rng)
            ),
            Doc(
                "1099_composite",
                y,
                "larkmoor-securities",
                "taxpayer",
                "5882-3017",
                figures={
                    "1a": _money(rng, 3_000, 6_000),
                    "1b": _money(rng, 2_000, 3_000),
                    "1": _money(rng, 100, 900),
                },
            ),
            Doc(
                "1099_div",
                y,
                "quarry-hill-funds",
                "spouse",
                "771204556",
                figures={"1a": _money(rng, 800, 1_500), "1b": _money(rng, 400, 800), "2a": None},
            ),
            Doc(
                "1099_r",
                y,
                "pellucid-retirement",
                "spouse",
                "30221958",
                figures={
                    "1": Decimal("12000.00"),
                    "2a": Decimal("12000.00"),
                    "4": Decimal("1200.00"),
                },
            ),
            Doc(
                "5498",
                y,
                "pellucid-retirement",
                "spouse",
                "30221958",
                figures={"1": Decimal("7000.00"), "5": _money(rng, 80_000, 120_000)},
            ),
            Doc(
                "1098",
                y,
                "gannet-home-lending",
                "taxpayer",
                "8800417262",
                figures={"1": _money(rng, 12_000, 16_000), "2": _money(rng, 380_000, 420_000),
                         "10": Decimal(5800 + 150 * (y - 2023))},  # escrowed (no rng: same set)
            ),
            Doc("ssa_1099", y, "social-security", "spouse", figures={"3": benefits, "5": benefits}),
            Doc(
                "1099_q",
                y,
                "ridgeway-college-savings",
                "taxpayer",
                "RCS0091877",
                figures={"1": _money(rng, 5_000, 9_000), "2": _money(rng, 500, 1_500)},
            ),
            Doc(
                "5498_sa",
                y,
                "willowbend-hsa",
                "taxpayer",
                "HSA4410982",
                figures={"2": Decimal("8300.00")},
            ),
            Doc("donation_ack", y, "literacy-circle", "taxpayer"),
            Doc("property_tax_bill", y, "town-of-hollis-ferry", "taxpayer"),
        ]
    docs.append(
        Doc(
            "1099_misc",
            2024,
            "ambergate-property",
            "taxpayer",
            "AMB-2207",
            figures={"1": _money(rng, 18_000, 22_000), "3": None},
        )
    )
    docs.append(
        Doc(
            "1099_nec",
            2025,
            "kestrel-media",
            "taxpayer",
            figures={"1": _money(rng, 4_000, 9_000), "4": Decimal("0.00")},
        )
    )

    def k1(year: int, entity: str, person: str = "taxpayer", **kw: object) -> Doc:
        doc_type = "k1_1120s" if entity == "nettlefield-design" else "k1_1065"
        if doc_type == "k1_1120s":
            figures = {"1": _whole(rng, 2_000, 9_000), "2": None, "4": _whole(rng, 10, 90)}
        else:
            figures = {
                "1": Decimal(0) if rng.random() < 0.4 else _whole(rng, -9_000, 9_000),
                "2": -_whole(rng, 1_000, 25_000),  # rental losses are common
                "5": None if rng.random() < 0.5 else _whole(rng, 5, 400),
                "19": _whole(rng, 1_000, 12_000),
            }
        printed = year - 1 if entity == FISCAL else None
        return Doc(doc_type, year, entity, person, figures=figures, printed_year=printed, **kw)

    ids = [e[0] for e in ENTITIES]
    docs += [k1(2023, e) for e in ids[:4]]
    docs += [k1(2024, e) for e in ids if e not in MISSING_2024]
    docs.append(k1(2024, "bluefen-apartments", corrected=True))
    docs += [
        k1(2025, e)
        for e in (
            "alderpoint-timber",
            "cinder-creek-energy",
            "elmstead-medical",
            "heronsgate-industrial",
            "mossgrove-lending",
            "nettlefield-design",
            FISCAL,
        )
    ]
    docs.append(k1(2025, "foxglove-ventures", "spouse"))
    docs.append(k1(2025, "granite-wren", "trust"))  # to the trust: filed in the trust's vault
    for y in (2024, 2025):
        docs.append(
            Doc(
                "k1_1041",
                y,
                "hollis-quill-legacy-trust",
                "taxpayer",
                figures={"1": _whole(rng, 100, 900), "2a": _whole(rng, 200, 1_500)},
            )
        )
    original = next(d for d in docs if d.doc_type == "1099_int" and d.year == 2025)
    docs.append(
        Doc(
            "1099_int",
            2025,
            original.issuer,
            "taxpayer",
            original.account,
            figures={**original.figures, "1": original.figures["1"] + 41},
            corrected=True,
        )
    )
    for d in docs:  # layouts the reader isn't tuned on: 2025's forms from these issuers
        if (
            d.year == 2025
            and not d.corrected
            and (
                d.doc_type in {"1099_int", "1099_div", "1099_r", "1098", "5498", "1099_nec"}
                or d.issuer == "sorrel-valley-health"
            )
        ):
            d.layout = "held_out"
    return docs


# ---------------------------------------------------------------------------------------------
# Rendering


def _fmt(value: Decimal | None, doc_type: str, layout: str) -> str:
    if value is None:
        return ""
    if doc_type in K1:  # tax software prints K-1s in whole dollars: "-12,345."
        return f"{int(value):,}."
    if layout == "held_out":
        return f"(${-value:,.2f})" if value < 0 else f"${value:,.2f}"
    return f"{value:,.2f}"


def _addr(issuer: str) -> tuple[str, str]:
    n = list(ISSUERS).index(issuer)
    return f"{100 + 7 * n} Commerce Way, Suite {n + 2}", f"Albany Springs, NY {12900 + n}"


def _recipient(d: Doc) -> tuple[str, str]:
    if d.person == "trust":
        return TRUST.name, TRUST.ssn
    p = PEOPLE[d.person]
    joint = d.person == "taxpayer" and d.doc_type in {"1099_int", "1099_composite", "1098"}
    return (f"{p.name} & {PEOPLE['spouse'].name}" if joint else p.name), p.ssn


def _put(
    page: pymupdf.Page, x: float, y: float, text: str, size: float = 9, font: str = "helv"
) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname=font)


def _right(
    page: pymupdf.Page, x1: float, y: float, text: str, size: float = 10, font: str = "cour"
) -> None:
    width = pymupdf.get_text_length(text, fontname=font, fontsize=size)
    _put(page, x1 - width, y, text, size, font)


def _year_line(d: Doc) -> str:
    if d.printed_year:
        return (
            f"For calendar year {d.printed_year}, or tax year beginning 07/01/{d.printed_year}"
            f" ending 06/30/{d.year}"
        )
    return ""


def _tuned(page: pymupdf.Page, d: Doc) -> None:
    """Laid out like the IRS form: captions in boxes, the values inside them."""
    spec, issuer = SPECS[d.doc_type], ISSUERS[d.issuer]
    name, tin = _recipient(d)
    printed = d.printed_year or d.year
    if d.corrected:
        _put(page, 330, 30, "CORRECTED", 9, "hebo")
    _put(page, 330, 46, f"{spec.title}  {printed}", 12, "hebo")
    _put(page, 330, 60, spec.subtitle, 7)
    if line := _year_line(d):
        _put(page, 330, 72, line, 6)
    if spec.issuer is None:  # the issuer's name is its letterhead
        _put(page, 36, 40, issuer.name.upper(), 12, "hebo")
    cells = [
        (36, 80, 284, 50, spec.issuer or "", [issuer.name, *_addr(d.issuer)]),
        (36, 130, 142, 30, spec.issuer_tin, [issuer.ein]),
        (178, 130, 142, 30, spec.recipient_tin, [tin]),
        (36, 160, 284, 44, spec.recipient, [name, *ADDRESS]),
    ]
    if spec.account:
        cells.append((36, 204, 284, 30, spec.account, [d.account or ""]))
    for x, y, w, h, caption, values in cells:
        page.draw_rect(pymupdf.Rect(x, y, x + w, y + h), width=0.5)
        _put(page, x + 3, y + 8, caption, 5.5)
        for i, value in enumerate(v for v in values if v):
            _put(page, x + 3, y + 20 + 11 * i, value, 9)
    for i, (box, caption) in enumerate(spec.boxes):  # two columns of boxes, like kformgen
        x, y, w = (330, 100 + 30 * (i // 2), 140) if i % 2 == 0 else (470, 100 + 30 * (i // 2), 130)
        page.draw_rect(pymupdf.Rect(x, y, x + w, y + 30), width=0.5)
        _put(page, x + 3, y + 9, box, 7)
        _put(page, x + 14, y + 9, caption.format(year=d.year), 6)
        if box in d.figures:
            _right(page, x + w - 6, y + 24, _fmt(d.figures[box], d.doc_type, d.layout))
    _put(page, 36, 760, f"{spec.title} (keep for your records)", 6)


def _held_out(page: pymupdf.Page, d: Doc) -> None:
    """A statement the issuer designed itself: letterhead, a summary, a table of boxes."""
    spec, issuer = SPECS[d.doc_type], ISSUERS[d.issuer]
    name, tin = _recipient(d)
    _put(page, 36, 50, issuer.name, 16, "hebo")
    for i, line in enumerate(_addr(d.issuer)):
        _put(page, 36, 66 + 12 * i, line, 9)
    _put(page, 36, 120, f"Tax year {d.year}  -  {spec.title}  {spec.subtitle}", 11, "hebo")
    _put(page, 36, 160, "Prepared for", 8)
    for i, line in enumerate([name, *ADDRESS]):
        _put(page, 36, 174 + 12 * i, line, 10)
    if d.account:
        _put(page, 330, 174, f"Account ending in {d.account[-4:]}", 10)
    _put(page, 330, 188, f"Your taxpayer ID  ***-**-{tin[-4:]}", 10)
    _put(page, 330, 202, f"Our federal ID  {issuer.ein}", 10)
    for i, (box, caption) in enumerate(spec.boxes):
        y = 260 + 20 * i
        _put(page, 36, y, f"Box {box}", 10)
        _put(page, 80, y, caption.format(year=d.year), 10)
        _right(page, 570, y, _fmt(d.figures.get(box), d.doc_type, d.layout) or "-", 10, "helv")
    _put(
        page,
        36,
        740,
        "This is important tax information and is being furnished to the Internal Revenue Service.",
        7,
    )


def _letter(page: pymupdf.Page, d: Doc, rng: random.Random) -> None:
    issuer, y = ISSUERS[d.issuer], d.year
    _put(page, 36, 50, issuer.name, 16, "hebo")
    _put(page, 36, 66, _addr(d.issuer)[0] + ", " + _addr(d.issuer)[1], 9)
    if d.doc_type == "donation_ack":
        lines = [
            f"December 12, {y}",
            "",
            FILERS["household"],
            *ADDRESS,
            "",
            f"Dear {FILERS['household']},",
            "",
            f"Thank you for your generous gift of ${rng.randint(5, 30) * 100:,}.00 received "
            f"on December 3, {y}.",
            "No goods or services were provided in exchange for your contribution.",
            f"Our federal tax ID is {issuer.ein}. Please keep this letter for your records.",
        ]
    else:
        lines = [
            f"{y} Property Tax Bill",
            "",
            "Parcel 112.04-1-9   41 Larkspur Lane",
            f"Owner: {PEOPLE['taxpayer'].name.upper()}",
            "",
            f"First installment due January 31, {y}   ${rng.randint(4, 6):,}450.00",
            f"Second installment due July 31, {y}   ${rng.randint(4, 6):,}450.00",
            "Pay online, by mail, or in person at Town Hall.",
        ]
    for i, line in enumerate(lines):
        _put(page, 36, 110 + 14 * i, line, 10)


def _pdf(draw: Callable[..., None], *args: object) -> bytes:
    pdf = pymupdf.open()
    draw(pdf.new_page(width=612, height=792), *args)
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return data


def _text_pdf(pages: list[list[str]]) -> bytes:
    pdf = pymupdf.open()
    for lines in pages:
        page = pdf.new_page(width=612, height=792)
        for i, line in enumerate(lines):
            _put(page, 40, 50 + 14 * i, line, 9)
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return data


def _render(d: Doc, rng: random.Random) -> bytes:
    if d.doc_type in {"donation_ack", "property_tax_bill"}:
        return _pdf(_letter, d, rng)
    return _pdf(_held_out if d.layout == "held_out" else _tuned, d)


# ---------------------------------------------------------------------------------------------
# Returns


def _total(docs: list[Doc], year: int, doc_type: str, box: str) -> Decimal:
    return sum(
        (
            d.figures.get(box) or Decimal(0)
            for d in docs
            if d.year == year and d.doc_type == doc_type and not d.corrected
        ),
        Decimal(0),
    )


def _form_1040(year: int, lines: list[tuple[str, Decimal]]) -> list[str]:
    t, s, k = PEOPLE["taxpayer"], PEOPLE["spouse"], PEOPLE["dependent"]
    head = [
        f"Form 1040   Department of the Treasury - Internal Revenue Service   {year}",
        "U.S. Individual Income Tax Return",
        f"For the year Jan. 1-Dec. 31, {year}, or other tax year beginning",
        "Filing Status  [ ] Single  [X] Married filing jointly  [ ] Married filing separately"
        "  [ ] Head of household",
        "Your first name and middle initial      Last name           Your social security number",
        f"{t.name.split()[0]}                                  {t.name.split()[1]}"
        f"               {t.ssn}",
        "If joint return, spouse's first name and middle initial   Last name   Spouse's SSN",
        f"{s.name.split()[0]}                                  {s.name.split()[1]}"
        f"               {s.ssn}",
        f"Home address (number and street)   {ADDRESS[0]}",
        "City, town, or post office   Hollis Ferry      State  NY      ZIP code  13999",
        "Dependents   (1) First name   Last name   (2) Social security number   (3) Relationship",
        f"             {k.name.split()[0]}   {k.name.split()[1]}   {k.ssn}   Child",
        "",
    ]
    return head + [f"{label} . . . . . . . .  {int(v):,}." for label, v in lines]


def returns(docs: list[Doc]) -> list[tuple[bytes, dict]]:
    """The 2024 return packet (1040 with Schedules A, B, E and a continuation statement), the
    2024 New York return and a bare 2023 1040, with what setup should suggest from each."""
    y = 2024
    interest = _total(docs, y, "1099_int", "1") + _total(docs, y, "1099_composite", "1")
    dividends = _total(docs, y, "1099_composite", "1a") + _total(docs, y, "1099_div", "1a")
    page1 = _form_1040(
        y,
        [
            ("1a Total amount from Form(s) W-2, box 1", _total(docs, y, "w2", "1")),
            ("2b Taxable interest", interest),
            ("3b Ordinary dividends", dividends),
            ("4a IRA distributions", _total(docs, y, "1099_r", "1")),
            ("6a Social security benefits", _total(docs, y, "ssa_1099", "5")),
            ("26 2024 estimated tax payments and amount applied from 2023 return", Decimal(4000)),
        ],
    )
    sched_a = [
        f"SCHEDULE A (Form 1040)   Itemized Deductions   {y}",
        "",
        f"5b State and local real estate taxes . . .  {int(_total(docs, y, '1098', '10')):,}.",
        "5c State and local personal property taxes . . .  420.",
        "8a Home mortgage interest and points reported to you on Form 1098 . . .  "
        f"{int(_total(docs, y, '1098', '1')):,}.",
        "11 Gifts by cash or check . . .  2,500.",
    ]
    payer = {i: ISSUERS[i].name for i in ISSUERS}
    sched_b = [
        f"SCHEDULE B (Form 1040)   Interest and Ordinary Dividends   {y}",
        "",
        "Part I  Interest",
        "1 List name of payer",
        f"   {payer['tamarack-savings-bank']}   {int(_total(docs, y, '1099_int', '1')):,}.",
        f"   {payer['larkmoor-securities']}   {int(_total(docs, y, '1099_composite', '1')):,}.",
        "",
        "Part II  Ordinary Dividends",
        "5 List name of payer",
        f"   {payer['larkmoor-securities']}   {int(_total(docs, y, '1099_composite', '1a')):,}.",
        f"   {payer['quarry-hill-funds']}   {int(_total(docs, y, '1099_div', '1a')):,}.",
    ]
    rows = [
        f"{chr(65 + i)}  {name}   {ps}   {ein}" for i, (_, name, ein, ps) in enumerate(ENTITIES)
    ]
    legacy = ISSUERS["hollis-quill-legacy-trust"]
    sched_e = [
        f"Schedule E (Form 1040) {y}   Page 2",
        "Part II  Income or Loss From Partnerships and S Corporations",
        "28  (a) Name    (b) Enter P for partnership; S for S corporation    "
        "(d) Employer identification number",
        *rows[:4],
        "    See Statement 1 for additional entities",
        "",
        "Part III  Income or Loss From Estates and Trusts",
        "33  (a) Name    (b) Employer identification number",
        f"A  {legacy.name}   {legacy.ein}",
    ]
    statement = [
        f"Statement 1 - Schedule E, Part II, Line 28 (continued)   Tax year {y}",
        "",
        *rows[4:],
    ]
    packet = _text_pdf([page1, sched_a, sched_b, sched_e, statement])
    named = [
        "tamarack-savings-bank",
        "larkmoor-securities",
        "quarry-hill-funds",
        *[e[0] for e in ENTITIES],
        legacy.id,
    ]
    expect_2024 = {
        "doc_type": "tax_return",
        "form": "1040",
        "year": y,
        "filer": "household",
        "filing_status": "married_filing_jointly",
        "names": {p: PEOPLE[p].name for p in PEOPLE},
        "state": "NY",  # from the address: a suggestion to confirm
        "named_sources": named,
        "general_rows": ["w2", "1099_r", "ssa_1099", "1098"],
        "missing_documents": MISSING_2024,  # listed on the return, no 2024 K-1 in the set
    }
    ny = _text_pdf(
        [
            [
                f"Form IT-201   Resident Income Tax Return   New York State   {y}",
                f"For the full year January 1, {y}, through December 31, {y}",
                f"Your first name  {PEOPLE['taxpayer'].name}   Spouse's first name  "
                f"{PEOPLE['spouse'].name}",
                f"Mailing address  {ADDRESS[0]}   {ADDRESS[1]}",
                "Filing status  (2) [X] Married filing joint return",
                f"17 Federal adjusted gross income . . . . . {int(_total(docs, y, 'w2', '1')):,}.",
            ]
        ]
    )
    expect_ny = {
        "doc_type": "tax_return",
        "form": "IT-201",
        "year": y,
        "filer": "household",
        "state": "NY",
        "resident": True,
    }
    bare = _text_pdf(
        [
            _form_1040(
                2023,
                [
                    ("1a Total amount from Form(s) W-2, box 1", _total(docs, 2023, "w2", "1")),
                    ("2b Taxable interest", _total(docs, 2023, "1099_int", "1")),
                    ("3b Ordinary dividends", _total(docs, 2023, "1099_div", "1a")),
                ],
            )
        ]
    )
    expect_bare = {
        "doc_type": "tax_return",
        "form": "1040",
        "year": 2023,
        "filer": "household",
        "filing_status": "married_filing_jointly",
        "names": {p: PEOPLE[p].name for p in PEOPLE},
        "state": "NY",
        "named_sources": [],
        "general_rows": ["w2", "1099_int", "1099_div"],
    }
    return [(packet, expect_2024), (ny, expect_ny), (bare, expect_bare)]


# ---------------------------------------------------------------------------------------------
# Scans, photos, bad files


def scan(data: bytes, angle: float = 0.7) -> bytes:
    """An image-only PDF: each page printed, slightly skewed, and scanned in grey."""
    src, out = pymupdf.open(stream=data), pymupdf.open()
    for i in range(len(src)):
        work = pymupdf.open()
        page = work.new_page(width=612, height=792)
        page.show_pdf_page(page.rect, src, i, rotate=angle)
        pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        out.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), pixmap=pix)
        work.close()
    result = out.tobytes(garbage=3, deflate=True)
    src.close()
    out.close()
    return result


def photo(data: bytes, angle: float = 3.0) -> bytes:
    """A phone photo of the first page: tilted, on a grey table, as JPEG."""
    src, work = pymupdf.open(stream=data), pymupdf.open()
    page = work.new_page(width=700, height=900)
    page.draw_rect(page.rect, color=None, fill=(0.55, 0.55, 0.5))
    page.show_pdf_page(pymupdf.Rect(44, 54, 656, 846), src, 0, rotate=angle)
    jpeg = page.get_pixmap(dpi=110).tobytes("jpg", jpg_quality=70)
    src.close()
    work.close()
    return jpeg


def _protected(data: bytes) -> bytes:
    pdf = pymupdf.open(stream=data)
    out = pdf.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="sample-owner", user_pw="sample-user"
    )
    pdf.close()
    return out


# ---------------------------------------------------------------------------------------------


def _answer(d: Doc, kind: str, file: str) -> dict:
    out: dict = {
        "file": file,
        "kind": kind,
        "layout": d.layout,
        "doc_type": d.doc_type,
        "year": d.year,
        "issuer": d.issuer,
        "issuer_name": ISSUERS[d.issuer].name,
        "account": d.account[-4:] if d.account else None,
        "person": d.person,
        "filer": d.filer,
        "corrected": d.corrected,
        "figures": {b: (None if v is None else str(v)) for b, v in d.figures.items()},
    }
    if d.printed_year:
        out["printed_year"] = d.printed_year
    if kind != "text":
        out["copy_of"] = d.file
    return out


def generate(out: Path, seed: int = 2025) -> dict:
    """Write the set into `out` (created if needed); returns what expected.yaml holds."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    docs = plan(rng)
    rendered = [(d, _render(d, rng)) for d in docs]
    filed = returns(docs)
    letter = _text_pdf(
        [
            [
                "Hollis Ferry Garden Club",
                "Spring Newsletter 2025",
                "",
                "Our plant sale is on May 10. Volunteers are welcome.",
            ]
        ]
    )
    # Neutral, shuffled file names, as downloads usually are: the reader must read the content.
    order = list(range(len(rendered) + len(filed) + 1))
    rng.shuffle(order)
    names = [f"doc-{n + 1:03d}.pdf" for n in order]
    files: dict[str, bytes] = {}
    answers: list[dict] = []
    for (d, data), name in zip(rendered, names, strict=False):
        d.file = name
        files[name] = data
        answers.append(_answer(d, "text", name))
    for (data, expect), name in zip(filed, names[len(rendered) :], strict=False):
        files[name] = data
        answers.append({"file": name, "kind": "text", "layout": "tuned", **expect})
    files[names[-1]] = letter
    others = [{"file": names[-1], "problem": "not_tax"}]

    # Scanned and photographed copies: one of each type from 2025, and a few photos.
    scans: dict[str, Doc] = {}
    for d, _ in rendered:
        if d.year == 2025 and d.layout == "tuned" and not d.corrected:
            scans.setdefault(d.doc_type, d)
    for i, d in enumerate(scans.values(), 1):
        name = f"scan-{i:03d}.pdf"
        files[name] = scan(files[d.file])
        answers.append(_answer(d, "scan", name))
    for i, d in enumerate(list(scans.values())[::3], 1):
        name = f"IMG_{4100 + i}.jpg"
        files[name] = photo(files[d.file])
        answers.append(_answer(d, "photo", name))
    packet = next(a["file"] for a in answers if a.get("form") == "1040" and a["year"] == 2024)
    files["scan-return.pdf"] = scan(files[packet], 0.4)
    answers.append(
        {
            **next(a for a in answers if a["file"] == packet),
            "file": "scan-return.pdf",
            "kind": "scan",
            "copy_of": packet,
        }
    )

    # A byte-for-byte copy, and files that can't be read.
    w2 = next(d for d, _ in rendered if d.doc_type == "w2" and d.year == 2025)
    files[f"{w2.file[:-4]} (1).pdf"] = files[w2.file]
    others.append(
        {"file": f"{w2.file[:-4]} (1).pdf", "problem": "duplicate", "duplicate_of": w2.file}
    )
    files["statement.pdf"] = files[w2.file][:700]
    others.append({"file": "statement.pdf", "problem": "corrupt"})
    files["protected.pdf"] = _protected(files[w2.file])
    others.append({"file": "protected.pdf", "problem": "password"})
    files["tax-documents.pdf"] = (
        b"<!DOCTYPE html><html><head><title>Sign in</title></head>"
        b"<body>Your session has expired.</body></html>"
    )
    others.append({"file": "tax-documents.pdf", "problem": "not_pdf"})
    files["notes.txt"] = b"Ask the preparer about the estimated payments.\n"
    others.append({"file": "notes.txt", "problem": "unsupported"})

    for name, data in files.items():
        (out / name).write_bytes(data)
    expected = {
        "about": "Fictional sample set. Every value is invented.",
        "seed": seed,
        "new_types": NEW_TYPES,
        "people": {p.id: p.name for p in PEOPLE.values()},
        "filers": FILERS,
        "issuers": {i.id: i.name for i in ISSUERS.values()},
        "documents": answers,
        "others": others,
    }
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    with (out / "expected.yaml").open("w") as f:
        yaml.dump(expected, f)
    return expected


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m tests.samples.generate OUT_DIR")
    result = generate(Path(sys.argv[1]))
    print(
        f"{len(result['documents'])} documents and {len(result['others'])} other files "
        f"written to {sys.argv[1]}"
    )
