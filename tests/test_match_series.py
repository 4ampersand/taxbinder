"""Several funds of one sponsor: the fund's number ("II", "3") decides between them."""

from __future__ import annotations

from dataclasses import replace

from taxbinder.ingest.classify import classify
from taxbinder.ingest.match import fund_numbers, match
from taxbinder.models import Expectation, Institution, Source

INST = [Institution(id="saltmarsh", name="Saltmarsh")]


def _rows(*names: str) -> tuple[list[Source], list[Expectation]]:
    srcs, exps = [], []
    for n in names:
        slug = n.lower().replace(" ", "-")
        s = Source(id=f"saltmarsh.{slug}", institution="saltmarsh", name=n, owner="me")
        srcs.append(s)
        exps.append(Expectation(id=f"2025/{s.id}.k1", year=2025, source=s.id, doc_type="k1_1065"))
    return srcs, exps


def _best(filename: str, text: str, *names: str) -> str | None:
    srcs, exps = _rows(*names)
    c = classify(text, filename, INST, 2026, [])
    m = match(c, text, filename, exps, srcs, INST)
    return m.best.source.name if m.best else None


def test_fund_numbers() -> None:
    assert fund_numbers("Saltmarsh Value-Add Fund II LP") == {2}
    assert fund_numbers("FUND III, L.P.") == {3}
    assert fund_numbers("Fund 3") == {3}
    assert fund_numbers("Fund_IV_K1.pdf") == {4}
    assert fund_numbers("fund i") == set()  # lower-case 'i' isn't a numeral
    assert fund_numbers("Opportunity Fund investments") == set()


def test_the_number_decides() -> None:
    k1 = "Schedule K-1 (Form 1065) 2025 Saltmarsh Value-Add Fund II, LP"
    name = "2025 Saltmarsh Value-Add Fund II LP K-1.pdf"
    # a row with the same number wins
    assert _best(name, k1, "Saltmarsh Fund II", "Saltmarsh Fund III") == "Saltmarsh Fund II"
    # a row without a number beats one with a different number
    assert _best(name, k1, "Saltmarsh Value Added Fund", "Saltmarsh Value Added Fund III") == (
        "Saltmarsh Value Added Fund"
    )
    # no number on the document: still a tie, so no guess
    plain = "Schedule K-1 (Form 1065) 2025 Saltmarsh"
    assert _best("Saltmarsh K-1.pdf", plain, "Saltmarsh Fund II", "Saltmarsh Fund III") is None
    # one row only: unchanged
    assert _best(name, k1, "Saltmarsh Value Added Fund") == "Saltmarsh Value Added Fund"


def test_account_digits_in_the_file_name_pick_the_row() -> None:
    brokers = [Institution(id="broker", name="Broker")]
    srcs = [
        Source(id="broker.joint-4417", institution="broker", name="Consolidated 4417", owner="me"),
        Source(id="broker.trust-4418", institution="broker", name="Consolidated 4418", owner="me"),
    ]
    exps = [
        Expectation(id=f"2024/{s.id}.1099", year=2024, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2024 Consolidated Tax Statement Broker Form 1099-B"
    name = "2024 Broker-123-454418-111.pdf"  # the account number ends 4418? no: "454418"
    c = classify(text, name, brokers, 2026, [])
    m = match(c, text, name, exps, srcs, brokers)
    assert m.best and m.best.source.id == "broker.trust-4418"
    other = "2024 Broker-123-994417.pdf"
    m = match(classify(text, other, brokers, 2026, []), text, other, exps, srcs, brokers)
    assert m.best and m.best.source.id == "broker.joint-4417"


def test_a_row_named_after_a_person_needs_more_than_the_name() -> None:
    gym = [Institution(id="hubline", name="HUBLINE"), Institution(id="broker", name="Broker")]
    srcs = [
        Source(id="hubline.rowan", institution="hubline", name="Rowan", owner="rowan"),
        Source(id="broker.main-4417", institution="broker", name="Main 4417", owner="me"),
    ]
    exps = [
        Expectation(id=f"2024/{s.id}.1099", year=2024, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2024 Consolidated Tax Statement  Account holder: Rowan Example"
    name = "statement.pdf"
    c = classify(text, name, gym, 2026, [])
    m = match(c, text, name, exps, srcs, gym, people=["Rowan Example"])
    assert m.best is None or m.best.source.id != "hubline.rowan"


def test_account_digits_split_by_dashes() -> None:
    brokers = [Institution(id="tidemark", name="tidemark")]
    srcs = [
        Source(id="tidemark.trust-2291", institution="tidemark", name="Trust 2291", owner="me"),
        Source(
            id="tidemark.stockplan-7705", institution="tidemark", name="StockPlan 7705", owner="me"
        ),
    ]
    exps = [
        Expectation(id=f"2024/{s.id}.1099", year=2024, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2024 Consolidated Tax Statement"
    for name, want in (
        ("2024 Tidemark-Trust-258-612291.pdf", "tidemark.trust-2291"),
        ("2024 tidemark_StockPlan-123-457705.pdf", "tidemark.stockplan-7705"),
    ):
        m = match(classify(text, name, brokers, 2026, []), text, name, exps, srcs, brokers)
        assert m.best and m.best.source.id == want, name


def test_form_and_year_in_a_file_name_are_not_an_account() -> None:
    """'k1 2024_CORBEL KVLD.pdf' once matched a row whose account ends in 2024."""
    from taxbinder.models.registry import Identifier

    insts = [Institution(id="corbel", name="Corbel"), Institution(id="cfc", name="CFC")]
    srcs = [
        Source(id="corbel.kvld", institution="corbel", name="KVLD", owner="me"),
        Source(
            id="cfc.ashcombe",
            institution="cfc",
            name="Ashcombe Partners",
            owner="me",
            identifiers=[Identifier(number="00-1112024", kind="primary")],
        ),
    ]
    exps = [
        Expectation(id=f"2024/{s.id}.k1", year=2024, source=s.id, doc_type="k1_1065") for s in srcs
    ]
    text = "Schedule K-1 (Form 1065) 2024 Corbel KVLD"
    name = "k1 2024_CORBEL KVLD.pdf"
    m = match(classify(text, name, insts, 2026, []), text, name, exps, srcs, insts)
    assert m.best and m.best.source.id == "corbel.kvld"


def test_four_digit_account_endings_but_not_years() -> None:
    brokers = [Institution(id="tidemark", name="tidemark")]
    srcs = [
        Source(
            id="tidemark.locpledged-5503",
            institution="tidemark",
            name="LOCPledged 5503",
            owner="me",
        ),
        Source(
            id="tidemark.keystep-2255",
            institution="tidemark",
            name="Keystep 2255",
            owner="me",
        ),
        Source(id="tidemark.trust-2023", institution="tidemark", name="Trust 2023", owner="me"),
    ]
    exps = [
        Expectation(id=f"2023/{s.id}.1099", year=2023, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2023 Consolidated 1099 Tax Statement"
    for name, want in (
        ("2023-Tidemark-LOCP-5503.pdf", "tidemark.locpledged-5503"),
        ("2023-Tidemark-Keystep-2255-Divs.pdf", "tidemark.keystep-2255"),
    ):
        m = match(classify(text, name, brokers, 2026, []), text, name, exps, srcs, brokers)
        assert m.best and m.best.source.id == want, name
    plain = "2023-Tidemark-statement.pdf"  # the year must not pick the account ending in 2023
    m = match(classify(text, plain, brokers, 2026, []), text, plain, exps, srcs, brokers)
    assert m.best is None


def test_an_account_ending_on_another_sponsors_row_does_not_count() -> None:
    """An TideMark file whose account ends like an Copperleaf Card number is not the Copperleaf Card's."""
    from taxbinder.models.registry import Identifier

    insts = [
        Institution(id="tidemark", name="Tidemark"),
        Institution(id="copperleaf-card", name="Copperleaf Card"),
    ]
    srcs = [
        Source(id="tidemark.trust-2291", institution="tidemark", name="Trust 2291", owner="me"),
        Source(
            id="copperleaf-card.gs",
            institution="copperleaf-card",
            name="Copperleaf Card",
            owner="me",
            identifiers=[Identifier(number="5555 4444 3333 7799", kind="primary")],
        ),
    ]
    exps = [
        Expectation(id=f"2023/{s.id}.1099", year=2023, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2023 Consolidated 1099 Tax Statement"
    name = "2023-Tidemark-7799.pdf"
    m = match(classify(text, name, insts, 2026, []), text, name, exps, srcs, insts)
    assert m.best is None or m.best.source.id != "copperleaf-card.gs"


def test_a_name_with_its_account_digits_matches_without_them() -> None:
    """Rows named after the sponsor ('tidemark-…') must not all match an TideMark file."""
    insts = [Institution(id="tidemark", name="Tidemark")]
    srcs = [
        Source(
            id="tidemark.keystep-2255",
            institution="tidemark",
            name="Keystep-2255",
            owner="me",
        ),
        Source(
            id="tidemark.locpledged-5503",
            institution="tidemark",
            name="tidemark-LOCPledged-5503",
            owner="me",
        ),
        Source(
            id="tidemark.trust-2291", institution="tidemark", name="tidemark-Trust-2291", owner="me"
        ),
    ]
    exps = [
        Expectation(id=f"2023/{s.id}.1099", year=2023, source=s.id, doc_type="1099_composite")
        for s in srcs
    ]
    text = "2023 Consolidated 1099 Tax Statement"
    name = "2023-Tidemark-Keystep-5521-Divs.pdf"  # the account was renumbered since
    m = match(classify(text, name, insts, 2026, []), text, name, exps, srcs, insts)
    assert m.best and m.best.source.id == "tidemark.keystep-2255"


def test_a_row_under_another_issuer_is_only_suggested() -> None:
    """A row named "Interest" doesn't take every 1099-INT; a K-1 still finds its fund."""
    insts = [
        Institution(id="brackley", name="Brackley Wealth"),
        Institution(id="tamarack", name="Tamarack Savings Bank"),
    ]
    srcs = [Source(id="brackley.interest", institution="brackley", name="Interest", owner="me")]
    exps = [Expectation(id="2025/brackley.interest.1099-int", year=2025, source=srcs[0].id,
                        doc_type="1099_int")]  # fmt: skip
    text = "Form 1099-INT 2025 Interest Income\nPAYER'S name\n{}\n1 Interest income 12.00"

    def best(issuer: str) -> str | None:
        t = text.format(issuer)
        c = replace(classify(t, "1099-INT.pdf", insts, 2026, []), issuer=issuer)
        m = match(c, t, "1099-INT.pdf", exps, srcs, insts)
        assert [x.source.id for x in m.candidates] == ["brackley.interest"]  # still suggested
        return m.best.source.id if m.best else None

    assert best("tamarack") is None
    assert best("brackley") == "brackley.interest"
    # a K-1 prints the fund, not the sponsor: its name still picks the sponsor's row
    k1 = "Schedule K-1 (Form 1065) 2025 Saltmarsh Value-Add Fund II, LP"
    srcs, exps = _rows("Saltmarsh Value-Add Fund II")
    c = replace(classify(k1, "k1.pdf", INST, 2026, []), issuer="value-add")
    assert match(c, k1, "k1.pdf", exps, srcs, INST).best is not None
