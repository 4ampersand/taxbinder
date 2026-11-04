"""Across the years: key figures across years (fictional data)."""

from __future__ import annotations

import io
import re
from decimal import Decimal

import pytest
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture
from test_years import store  # noqa: F401 - fixture

from taxbinder import matrix
from taxbinder.models.registry import Identifier
from taxbinder.store import RecordStore
from taxbinder.web.app import AppState


def test_whole_dollars_and_parsing() -> None:
    assert matrix.whole(Decimal("4535.49")) == "4,535"
    assert matrix.whole(Decimal("4535.50")) == "4,536"
    assert matrix.whole(Decimal("-71880")) == "(71,880)"
    assert matrix.whole(None) == "—"
    assert matrix.parse_cell("4535 / 9,764.20", 2) == [Decimal("4535.00"), Decimal("9764.20")]
    assert matrix.parse_cell("(71,880)", 1) == [Decimal("-71880.00")]
    assert matrix.parse_cell("— / 12", 2) == [None, Decimal("12.00")]
    assert matrix.parse_cell("5", 3) == [Decimal("5.00"), None, None]
    assert matrix.parse_cell("", 2) == [None, None]
    with pytest.raises(ValueError, match="1 box"):
        matrix.parse_cell("1 / 2", 0)
    with pytest.raises(ValueError, match="not an amount"):
        matrix.parse_cell("abc", 1)
    assert matrix.boxes_of(" 1, 5 ,AE ") == ["1", "5", "AE"]


def _row(store: RecordStore, key: str):  # noqa: ANN202, F811
    return next(r for r in matrix.rows(store) if r.key == key)


def test_rows_years_and_cells(store: RecordStore) -> None:  # noqa: F811
    rows = matrix.rows(store)
    assert rows and all(r.cells for r in rows)  # every row with an amount
    first = rows[0]
    exp = next(
        e
        for e in store.list("expectation", 2025)
        if (e.source, e.doc_type) == (first.source.id, first.doc_type)
    )
    assert first.cells[2025].first == exp.amount  # the Checklist's Amount
    assert matrix.years(store) == [2025]

    # two boxes; a past year without checklist rows; the first box stays on the Checklist row
    matrix.set_boxes(store, first.source.id, first.doc_type, "1, 5", "t")
    c = matrix.set_cell(store, first.source.id, first.doc_type, 2025, "4535 / 9764", "t")
    assert c.text == "4,535 / 9,764"
    assert store.get("expectation", exp.id)[0].amount == Decimal("4535.00")
    matrix.set_cell(store, first.source.id, first.doc_type, 2022, "(8,315) / —", "t")
    assert matrix.years(store) == [2022, 2023, 2024, 2025]
    row = _row(store, first.key)
    assert row.cells[2022].text == "(8,315) / —" and row.cells[2023].text == ""
    matrix.set_first_year(store, 2020, "t")
    assert matrix.years(store)[0] == 2020

    # clearing a past year's cell removes the figure
    matrix.set_cell(store, first.source.id, first.doc_type, 2022, "", "t")
    assert _row(store, first.key).cells[2022].text == ""
    with pytest.raises(ValueError, match="2 box"):
        matrix.set_cell(store, first.source.id, first.doc_type, 2025, "1 / 2 / 3", "t")


def test_add_remove_rows_and_masked_id(store: RecordStore) -> None:  # noqa: F811
    first = matrix.rows(store)[0]
    matrix.remove_row(store, first.source.id, first.doc_type, "t")
    assert first.key not in {r.key for r in matrix.rows(store)}
    with pytest.raises(ValueError, match="isn't in Across the years"):
        matrix.remove_row(store, first.source.id, first.doc_type, "t")
    matrix.add_row(store, first.source.id, first.doc_type, "AE", "t")
    assert _row(store, first.key).boxes == ["AE"]
    with pytest.raises(ValueError, match="already a row"):
        matrix.add_row(store, first.source.id, first.doc_type, "", "t")
    src, rev = store.get("source", first.source.id)
    with store.operation("t", "ids"):
        store.put(
            src.model_copy(
                update={
                    "identifiers": [
                        Identifier(number="5801-1144", kind="primary"),
                        Identifier(number="00-1234567", kind="reference"),
                    ]
                }
            ),
            rev,
        )
    assert _row(store, first.key).ident == "…4567"  # the reference (EIN) first


def test_matrix_page_and_api(state: AppState) -> None:  # noqa: F811
    c = client(state)
    from taxbinder.years import new_year

    new_year(state.store, 2026, 2025, "test")  # a second year: the columns run newest first
    html = c.get("/years").text  # Across the years
    head = html.split("<thead>")[1].split("</thead>")[0]
    shown = [int(y) for y in re.findall(r'class="num">(\d{4})<', head)]
    assert shown == sorted(shown, reverse=True) and len(shown) > 1  # newest first
    assert "Across the years" in html and 'id="ay"' in html
    row = matrix.rows(state.store)[0]
    body = {"source": row.source.id, "doc_type": row.doc_type}
    r = c.post("/api/matrix/boxes", json={**body, "boxes": "1, 5"}, headers=ORIGIN)
    assert r.status_code == 200
    r = c.post(
        "/api/matrix/set", json={**body, "year": 2024, "value": "4535 / 9764"}, headers=ORIGIN
    )
    assert r.status_code == 200 and r.json()["text"] == "4,535 / 9,764" and "undo" in r.json()
    html = c.get("/years").text  # the second box (the preparer's numbers): a sub-row
    sub = html.split(f'data-of="{row.key}"')[1].split("</tr>")[0]
    assert '<span class="">9,764</span>' in sub and "4,535</span>" not in sub
    assert '<span class="">4,535</span>' in html and "4,535 / " not in html
    r = c.post("/api/matrix/set", json={**body, "year": 2024, "box": 1, "value": "9800"},
               headers=ORIGIN)  # fmt: skip
    assert r.json()["part"] == ["9,800", False] and r.json()["text"] == "4,535 / 9,800"
    r = c.post(
        "/api/matrix/set", json={**body, "year": 2024, "value": "4535 / (9764)"}, headers=ORIGIN
    )
    assert r.json()["parts"] == [["4,535", False], ["(9,764)", True]]
    assert '<span class="neg">(9,764)</span>' in c.get("/years").text  # negatives in red
    assert '<span class="neg">(9,764)</span>' in c.get("/years").text.split('class="sub"')[1]
    bad = c.post("/api/matrix/set", json={**body, "year": 2024, "value": "x"}, headers=ORIGIN)
    assert bad.status_code == 400
    xlsx = c.get("/matrix.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    from openpyxl import load_workbook

    head = next(load_workbook(io.BytesIO(xlsx.content))["Across the years"].iter_rows(values_only=True))
    years = [v for v in head if isinstance(v, int)]
    assert years == sorted(years, reverse=True) and len(years) > 1  # newest first, as the page
    assert c.post("/api/matrix/remove", json=body, headers=ORIGIN).status_code == 200
    assert c.post("/api/matrix/add", json={**body, "boxes": "1"}, headers=ORIGIN).status_code == 200
    for path in ("/api/matrix/set", "/api/matrix/add", "/api/matrix/remove", "/api/matrix/boxes"):
        assert client(state, login=False).post(path, json={}, headers=ORIGIN).status_code == 401


def test_add_all_with_suggested_boxes(store: RecordStore) -> None:  # noqa: F811
    from taxbinder.models import Expectation, Institution, Source

    with store.operation("t", "fixture"):
        store.put(
            Institution(id="harbor", name="Harbor Realty Partners", kind="real_estate_sponsor"),
            None,
        )
        store.put(Institution(id="tech", name="Tech Growth"), None)
        for sid, inst, name in [
            ("harbor.harbor-fund-ii", "harbor", "Harbor Fund II"),
            ("harbor.harbor-fund-iii", "harbor", "Harbor Fund III"),
            ("tech.tech-ventures", "tech", "Tech Ventures"),
        ]:
            store.put(Source(id=sid, institution=inst, name=name, owner="me"), None)
            store.put(
                Expectation(id=f"2025/{sid}.k1", year=2025, source=sid, doc_type="k1_1065"), None
            )
    rows = {r.source.id: r for r in matrix.rows(store)}  # every expected document is a row
    assert rows["harbor.harbor-fund-ii"].boxes == ["2"]  # real estate sponsor → rental income
    assert rows["tech.tech-ventures"].boxes == ["1"]
    assert matrix.add_all(store, "t") == 0  # nothing left to add
    # the person's choice for one Harbor K-1 is reused for the next one
    matrix.remove_row(store, "harbor.harbor-fund-iii", "k1_1065", "t")
    matrix.set_boxes(store, "harbor.harbor-fund-ii", "k1_1065", "2, 20 Z", "t")
    matrix.add_row(store, "harbor.harbor-fund-iii", "k1_1065", "", "t")
    assert _row(store, "harbor.harbor-fund-iii|k1_1065").boxes == ["2", "20 Z"]


def test_add_all_endpoint(state: AppState) -> None:  # noqa: F811
    c = client(state)
    assert c.post("/api/matrix/add-all", headers=ORIGIN).status_code == 200
    assert 'id="ay-add-open"' in c.get("/years").text


def _k1_on_kettle(store: RecordStore, name: str = "kettle-k1.pdf", **boxes: str) -> str:  # noqa: F811
    from kformgen import k1
    from test_years import KETTLE

    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.review import link_document

    path = store.vault.inbox / name
    path.write_bytes(k1(**boxes))
    doc = ingest_file(store, path, "upload", "t").doc_id
    exp, _ = store.get("expectation", KETTLE)
    if not any(x.doc == doc for x in exp.evidence):
        link_document(store, doc, KETTLE, "t")
    return doc


def test_figures_read_from_pdfs_as_suggestions(store: RecordStore) -> None:  # noqa: F811
    from test_years import KETTLE

    exp, _ = store.get("expectation", KETTLE)
    key = f"{exp.source}|{exp.doc_type}"
    if key not in {r.key for r in matrix.rows(store)}:
        matrix.add_row(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_boxes(store, exp.source, exp.doc_type, "1, 2", "t")
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "", "t")  # start empty
    _k1_on_kettle(store)
    counts = matrix.read_pdfs(store, "t")
    assert counts["read"] >= 1 and counts["suggested"] >= 1
    cell = _row(store, key).cells[2025]
    assert cell.pending and cell.suggested == [Decimal("0"), Decimal("-12345")]
    assert cell.values == [None, None] and cell.text == "0 / (12,345)"  # shown, not saved
    assert "Read from the 2025 document" in _row(store, key).tip(2025)
    matrix.accept(store, exp.source, exp.doc_type, 2025, "t")
    cell = _row(store, key).cells[2025]
    assert cell.values == [Decimal("0"), Decimal("-12345")] and not cell.suggested
    assert store.get("expectation", KETTLE)[0].amount == 0  # the first box is the Checklist Amount
    assert matrix.read_pdfs(store, "t")["suggested"] == 0  # nothing new

    # a typed value that differs is flagged, never replaced; dismissing it sticks
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "0 / -15000", "t")
    assert matrix.read_pdfs(store, "t")["conflicts"] >= 1
    cell = _row(store, key).cells[2025]
    assert cell.conflict and cell.values[1] == Decimal("-15000")
    matrix.dismiss(store, exp.source, exp.doc_type, 2025, "t")
    assert matrix.read_pdfs(store, "t")["conflicts"] == 0  # that document is never suggested again
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "0 / ", "t")
    assert not _row(store, key).cells[2025].suggested


def test_accept_one_cell_takes_the_pdf_value(state: AppState) -> None:  # noqa: F811
    from test_years import KETTLE

    c = client(state)
    vault = state.store
    exp, _ = vault.get("expectation", KETTLE)
    body = {"source": exp.source, "doc_type": exp.doc_type}
    if f"{exp.source}|{exp.doc_type}" not in {r.key for r in matrix.rows(vault)}:
        c.post("/api/matrix/add", json={**body, "boxes": "2"}, headers=ORIGIN)
    c.post("/api/matrix/boxes", json={**body, "boxes": "2"}, headers=ORIGIN)
    c.post("/api/matrix/set", json={**body, "year": 2025, "value": "-15000"}, headers=ORIGIN)
    _k1_on_kettle(vault)
    assert matrix.read_pdfs(vault, "t")["conflicts"] >= 1
    html = c.get("/years").text
    assert 'data-act="accept"' in html and " conflict" in html
    # View sits with ✓ ✕, under them, never laid over them
    acts = html[html.index('<span class="acts">') :]
    assert 'class="link" data-view="' in acts[: acts.index("</span>")]
    r = c.post("/api/matrix/accept", json={**body, "year": 2025}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["text"] == "(12,345)"
    assert "undo" in r.json()  # undoing puts the typed value back
    assert (
        c.post("/api/matrix/accept", json={**body, "year": 2025}, headers=ORIGIN).status_code == 400
    )


def test_a_past_year_without_rule_parameters_loads(state: AppState) -> None:  # noqa: F811
    """2024 has rows but no rule parameters: arriving documents are filed and read anyway."""
    from kformgen import form

    from taxbinder.web.watcher import process_inbox
    from taxbinder.years import new_year

    new_year(state.store, 2024, 2025, "t")
    cells = [(330, 100, 140, "2", "Net rental real estate income (loss)", "-9,876.")]
    k1 = form(cells, ["Schedule K-1 (Form 1065) 2024", "Fernhollow properties", "Kettle 410"])
    import os
    import time

    pdf = state.vault_path / "inbox" / "Kettle410_2024_K1.pdf"
    pdf.write_bytes(k1)
    os.utime(pdf, (time.time() - 10, time.time() - 10))  # settled, as the watcher expects
    results = process_inbox(state)
    assert results and results[0].linked_to == "2024/fernhollow-properties.kettle-410.k1"


def test_add_row_dialog_points_to_the_checklist_when_nothing_is_left(state: AppState) -> None:  # noqa: F811
    c = client(state)
    c.post("/api/matrix/add-all", headers=ORIGIN)
    html = c.get("/years").text
    assert "Every document the binder expects already has a row." in html
    assert 'href="/#expect"' in html and 'id="ay-add-go"' not in html


def test_a_reading_that_doesnt_add_up_waits(store: RecordStore) -> None:  # noqa: F811
    """A K-1 whose qualified dividends exceed its ordinary dividends (6b > 6a): shown with
    "doesn't add up", and its boxes stay empty until it's looked at."""
    from kformgen import form
    from test_years import KETTLE

    from taxbinder.ingest.inbox import ingest_file
    from taxbinder.review import link_document

    k1 = form(
        [
            (330, 100, 140, "1", "Ordinary business income (loss)", "10."),
            (470, 100, 130, "14", "Self-employment earnings (loss)", ""),
            (330, 130, 140, "2", "Net rental real estate income (loss)", ""),
            (470, 130, 130, "15", "Credits", ""),
            (330, 160, 140, "6a", "Ordinary dividends", "100."),
            (470, 160, 130, "6b", "Qualified dividends", "500."),
        ],
        ["Schedule K-1 (Form 1065) 2025", "OMB No. 1545-0123"],
    )
    exp, _ = store.get("expectation", KETTLE)
    key = f"{exp.source}|{exp.doc_type}"
    if key not in {r.key for r in matrix.rows(store)}:
        matrix.add_row(store, exp.source, exp.doc_type, "6a, 6b", "t")
    matrix.set_boxes(store, exp.source, exp.doc_type, "6a, 6b", "t")
    matrix.set_cell(store, exp.source, exp.doc_type, 2025, "", "t")
    path = store.vault.inbox / "kettle-dividends.pdf"
    path.write_bytes(k1)
    doc = ingest_file(store, path, "upload", "t").doc_id
    if not any(x.doc == doc for x in store.get("expectation", KETTLE)[0].evidence):
        link_document(store, doc, KETTLE, "t")
    matrix.read_pdfs(store, "t")
    cell = _row(store, key).cells[2025]
    assert cell.suggested == [Decimal("100"), Decimal("500")] and cell.checks == ["6b <= 6a"]
    assert "Check it against the paper: 6b <= 6a." in _row(store, key).tip(2025)
    assert _row(store, key).cells[2025].values == [None, None]
    # looked at against the paper: ✓ keeps it (✕ would leave the boxes empty)
    from test_web import PORT

    html = client(AppState(vault_path=store.vault.root, store=store, port=PORT)).get("/years").text
    assert "It’s right: keep it" in html
    matrix.accept(store, exp.source, exp.doc_type, 2025, "t")
    assert _row(store, key).cells[2025].values == [Decimal("100"), Decimal("500")]


def test_w2_rows_follow_tax_withheld_as_sub_rows(state: AppState) -> None:  # noqa: F811
    """A W-2 row follows its key boxes (wages, federal tax withheld, state
    wages, state tax); the row shows wages, the others open beneath it, each edited alone. A
    row made before (on box 1 alone) is offered them, never changed silently."""
    c = client(state)
    vault = state.store
    w2 = next(r for r in matrix.rows(vault) if r.doc_type == "w2")
    assert w2.boxes == ["1", "2", "16", "17"]
    assert [name for _, _, name in w2.subs] == [
        "Federal income tax withheld", "State wages, tips, etc.", "State income tax"]
    body = {"source": w2.source.id, "doc_type": "w2"}
    r = c.post("/api/matrix/set", json={**body, "year": 2025, "box": 2, "value": "61250"},
               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 200 and r.json()["part"] == ["61,250", False]
    assert _row(vault, w2.key).cells[2025].values[2] == Decimal(61250)  # box 16 alone
    html = c.get("/years").text
    assert 'class="link open-sub' in html and 'id="ay-open-all"' in html
    assert f'data-of="{w2.key}" hidden' in html and "ay-key-boxes" not in html
    # a row from before: box 1 alone, the person never chose it
    matrix.set_boxes(vault, w2.source.id, "w2", "1", "t")
    layout, rev = matrix._layout(vault)
    rows = [x.model_copy(update={"own": False}) for x in layout.rows]
    with vault.operation("t", "an older row"):
        vault.put(layout.model_copy(update={"rows": rows}), rev)
    assert (w2.source.id, "w2") in matrix.short_of_key_boxes(vault)
    assert 'id="ay-key-boxes"' in c.get("/years").text
    assert c.post("/api/matrix/key-boxes", json={}, headers=ORIGIN).json()["changed"] >= 1
    assert _row(vault, w2.key).boxes == ["1", "2", "16", "17"]
    assert not matrix.short_of_key_boxes(vault)


def test_donations_across_the_years(state: AppState) -> None:  # noqa: F811
    """Gifts follow each charity through the years; a DAF grant isn't counted twice."""
    import io
    from datetime import date

    from openpyxl import load_workbook

    from taxbinder.donations import add_donation, by_year
    from taxbinder.years import new_year

    s = state.store
    new_year(s, 2026, 2025, "test")
    before = by_year(s)[1]
    add_donation(s, "Lantern Library", Decimal("100"), date(2025, 3, 2), "cash", "t")
    add_donation(s, "lantern  library", Decimal("50"), date(2025, 9, 1), "cash", "t")  # the same
    add_donation(s, "Lantern Library", Decimal("200"), date(2026, 4, 5), "cash", "t")
    add_donation(s, "Harbor Arts Trust", Decimal("300"), date(2026, 5, 6), "daf_grant", "t")
    given, total = by_year(s)
    lantern = next(g for g in given if g.name.casefold() == "lantern library")
    assert lantern.years == {2025: Decimal("150"), 2026: Decimal("200")}
    assert next(g for g in given if g.name == "Harbor Arts Trust").from_daf
    assert total[2026] - before.get(2026, 0) == Decimal("200")  # the grant stays out
    c = client(state)
    html = c.get("/years").text
    gifts = html.split('id="ay-gifts"')[1].split("</table>")[0]
    assert "Lantern Library" in gifts and "from your DAF" in gifts and "Total given" in gifts
    assert gifts.count("Lantern Library") == 1  # one row, however it was typed
    wb = load_workbook(io.BytesIO(c.get("/matrix.xlsx").content))
    rows = list(wb["Donations"].iter_rows(values_only=True))
    head, last = rows[0], rows[-1]
    assert last[0] == "Total given" and last[head.index(2026)] == total[2026]
