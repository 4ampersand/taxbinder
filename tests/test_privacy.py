"""Private details: stored encrypted, listed masked."""

from __future__ import annotations

from test_years import store  # noqa: F401 - fixture

from taxbinder.privacy import add_value, masked, remove_value, values
from taxbinder.store import RecordStore


def test_private_details_are_encrypted_and_masked(store: RecordStore) -> None:  # noqa: F811
    assert add_value(store, "name", "Maria  Example", "spouse", "t") == 1
    assert add_value(store, "name", "maria example", "", "t") == 1  # already there
    add_value(store, "address", "1234 Elm Court, Cedarville, CA 95014", "home", "t")
    add_value(store, "number", "5801-1144", "old account", "t")
    on_disk = (store.vault.records / "registry" / "privacy.yaml").read_text()
    assert (
        "Maria Example" not in on_disk and "Elm Court" not in on_disk and "5801-1144" not in on_disk
    )
    assert "ENC[age:" in on_disk and "spouse" in on_disk  # notes are plain labels
    got = values(store)
    assert [v.secret for v in got][0] == "Maria Example"  # whitespace normalised
    assert [masked(v) for v in got] == ["M… E…", "1… E… C… C… C… 9…", "…1144"]
    remove_value(store, 0, "t")
    assert [v.kind for v in values(store)] == ["address", "number"]
    index_rows = store._index.db.execute("select data from records where kind='privacy'").fetchall()
    assert "Elm Court" not in str(index_rows) and "5801-1144" not in str(index_rows)
