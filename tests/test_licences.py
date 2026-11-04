"""The third-party licences file: made at build time from what the app includes,
shown from About; a copy built without it says so."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_web import client, state  # noqa: F401 - `state` is a fixture

from taxbinder import help as helptext
from taxbinder.dev import licences
from taxbinder.web.app import AppState


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, list[licences.Item]]:
    if not (licences.ROOT / "build" / "engine" / "LICENSE").is_file():
        pytest.skip("no engine fetched (`make engine`)")
    out = tmp_path_factory.mktemp("licences") / helptext.LICENCES
    items = licences.build(out)
    return out.read_text(), items


def test_every_library_the_app_includes(made: tuple[str, list[licences.Item]]) -> None:
    text, items = made
    names = {i.name.lower() for i in items}
    assert {"python", "fastapi", "pydantic", "pyrage", "pypdfium2", "keyring", "uvicorn",
            "pyinstaller bootloader", "tauri", "wry"} <= names  # fmt: skip
    # never the development tools, nor TaxBinder itself (About says its licence)
    assert not names & {"pytest", "ruff", "pymupdf", "taxbinder", "pyinstaller"}
    # each with its licence text, but for the few published without one (checked by hand)
    assert all(i.texts for i in items if i.group == "Python" and i.name not in licences.WITHOUT_FILE)
    assert "onnxruntime" in names and "ThirdPartyNotices.txt" in text  # kept beside its code
    assert "Only the text-recognition models" in text  # rapidocr: its models, not its code
    assert "PYTHON SOFTWARE FOUNDATION LICENSE" in text
    assert "dulwich" in text and "used by TaxBinder under Apache-2.0" in text  # its choice, made
    assert "Used by: pypdfium2" in text and "pdfium.txt" in text  # PDFium's own libraries
    assert "darwin_x86_64" not in text and "linux" not in text.split("Licence texts")[0]
    assert text.count("Used by:") < sum(len(i.texts) for i in items)  # each text once


def test_about_links_to_the_list(state: AppState, tmp_path: Path,  # noqa: F811
                                 monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    listed = tmp_path / helptext.LICENCES
    listed.write_text(licences.render([
        licences.Item("Python", "fastapi", "1.0", "MIT", ["LICENSE\n\nThe MIT License <x>"]),
        licences.Item("Rust", "tauri", "2.0", "Apache-2.0 OR MIT", []),
    ]))  # fmt: skip
    monkeypatch.setenv("TAXBINDER_LICENCES", str(listed))
    c = client(state)
    assert "TaxBinder includes 2 other free software libraries" in c.get("/help").text
    page = c.get("/help/licences").text
    assert "fastapi 1.0: MIT" in page and "The MIT License &lt;x&gt;" in page  # shown as text
    assert "published without its licence file" in page

    monkeypatch.setenv("TAXBINDER_LICENCES", str(tmp_path / "none.txt"))
    assert "this copy was built without it" in c.get("/help").text
    assert "this copy was built without it" in c.get("/help/licences").text
    assert helptext.licences_file() is None


def test_a_bundled_package_missing_from_the_list_stops_the_build(tmp_path: Path) -> None:
    import pydantic
    import pytest as bundled_by_mistake

    work = tmp_path / "work"
    work.mkdir()
    files = [("pydantic", pydantic.__file__, "PYMODULE"),
             ("pytest", bundled_by_mistake.__file__, "PYMODULE")]  # fmt: skip
    (work / "PYZ-00.toc").write_text(repr(("PYZ-00.pyz", files)))
    (work / "PKG-00.toc").write_text(repr(("pkg", [])))
    assert licences.bundled(work) == {"pydantic", "pytest"}
    listed = tmp_path / helptext.LICENCES
    listed.write_text(licences.render([licences.Item("Python", "pydantic", "2", "MIT", ["x"])]))
    with pytest.raises(SystemExit, match="bundled but not in the licences file: pytest"):
        licences.check_bundle(work, listed)
    (work / "PYZ-00.toc").write_text(repr(("PYZ-00.pyz", files[:1])))
    licences.check_bundle(work, listed)  # everything listed: the build goes on
