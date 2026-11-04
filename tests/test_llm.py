"""The reading model's list, files and downloads, with invented files served on this
computer: nothing is downloaded from the internet in the tests."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_web import ORIGIN, client, state  # noqa: F401 - `state` is a fixture

from taxbinder import llm
from taxbinder.web.app import LOCAL_ONLY, AppState


def _entry(name: str, data: bytes) -> dict:
    return {"name": name, "url": f"https://huggingface.co/x/{name}",
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}  # fmt: skip


def _list(model: bytes, mmproj: bytes, version: int = 2) -> dict:
    return {"version": version, "models": [{
        "id": "tiny", "name": "Tiny", "maker": "Nobody", "licence": "Apache-2.0",
        "page": "https://huggingface.co/x", "min_memory_gb": 16,
        "files": {"model": _entry("tiny.gguf", model), "mmproj": _entry("mmproj-tiny.gguf", mmproj)},
    }]}  # fmt: skip


class _Ranges(SimpleHTTPRequestHandler):
    """Serves a folder, honouring `Range: bytes=N-` (as Hugging Face does)."""

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = Path(self.directory) / Path(self.path).name
        data = path.read_bytes()
        start = int(self.headers["Range"].split("=")[1].rstrip("-")) if "Range" in self.headers else 0
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    files = tmp_path / "served"
    files.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Ranges, directory=str(files)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def local(req: urllib.request.Request, timeout: float = 60):  # noqa: ANN202
        assert req.full_url.startswith("https://huggingface.co/")  # only ever there
        return urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(req.full_url.replace("https://huggingface.co/x", base),
                                   headers=dict(req.header_items())), timeout=timeout)  # fmt: skip

    monkeypatch.setattr(llm, "_open", local)
    yield files
    server.shutdown()


def test_the_apps_own_list() -> None:
    models = llm.parse(json.loads(llm.BUILT_IN.read_text()))
    assert [m.id for m in models] == ["qwen3-vl-4b-q4", "qwen3-vl-8b-q4"]
    assert all(m.licence == "Apache-2.0" and m.mmproj and m.min_memory_gb == 16 for m in models)
    assert all(f.url.startswith("https://huggingface.co/Qwen/") and "/resolve/main/" not in f.url
               for m in models for f in m.files)  # fmt: skip - each pinned to a revision
    assert llm.suggested(models, 16).id == "qwen3-vl-4b-q4" and llm.suggested(models, 8) is None


def test_a_list_only_from_where_models_come_from() -> None:
    bad = _list(b"a", b"b")
    bad["models"][0]["files"]["model"]["url"] = "https://example.com/tiny.gguf"
    with pytest.raises(ValueError, match="somewhere else"):
        llm.parse(bad)
    bad = _list(b"a", b"b")
    bad["models"][0]["files"]["model"]["name"] = "../../tiny.gguf"
    with pytest.raises(ValueError):
        llm.parse(bad)
    with pytest.raises(ValueError, match="not an AI model list"):
        llm.parse({"models": [{"id": "x"}]})


def test_download_resumes_and_is_checked(served: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    model, mmproj = b"M" * 3_000_000, b"P" * 100_000
    (served / "tiny.gguf").write_bytes(model)
    (served / "mmproj-tiny.gguf").write_bytes(mmproj)
    m = llm.parse(_list(model, mmproj))[0]
    where = tmp_path / "models"
    where.mkdir()
    (where / "tiny.gguf.part").write_bytes(model[:1_234_567])  # an earlier attempt stopped here
    d = llm.Download(m, where)
    d.start()
    d.wait(30)
    assert d.finished and not d.error and d.done == m.size
    assert (where / "tiny.gguf").read_bytes() == model and llm.installed(where, m)
    assert not list(where.glob("*.part"))

    # a file that isn't what the list says is thrown away, and nothing is installed
    llm.delete(where, m)
    (served / "mmproj-tiny.gguf").write_bytes(b"X" * 100_000)
    d = llm.Download(m, where)
    d.start()
    d.wait(30)
    assert "didn't arrive intact" in d.error and not llm.installed(where, m)
    assert not (where / "mmproj-tiny.gguf").exists() and not (where / "mmproj-tiny.gguf.part").exists()


def test_a_newer_list_only_with_4ampersands_signature(served: Path, tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw).hex()  # fmt: skip
    data = json.dumps(_list(b"a", b"b", version=9)).encode()
    (served / "list.json").write_bytes(data)
    (served / "list.json.sig").write_bytes(Ed25519PrivateKey.generate().sign(data))  # not ours
    where = tmp_path / "models"
    url = "https://huggingface.co/x/list.json"
    with pytest.raises(ValueError, match="isn't signed"):
        llm.check(where, url, public)
    assert [m.id for m in llm.catalogue(where, public)][0] == "qwen3-vl-4b-q4"  # the app's own
    (served / "list.json.sig").write_bytes(key.sign(data))
    assert [m.id for m in llm.check(where, url, public)] == ["tiny"]
    assert [m.id for m in llm.catalogue(where, public)] == ["tiny"]
    (where / "list.json").write_bytes(data.replace(b"Tiny", b"Evil"))  # changed on disk since
    assert llm.catalogue(where, public)[0].id == "qwen3-vl-4b-q4"
    with pytest.raises(ValueError, match="can't check"):
        llm.check(where, url, None)  # no key in this copy yet


def test_which_model_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    where = tmp_path / "models"
    models = llm.catalogue(where)
    small, big = models
    assert llm.current(where) is None  # nothing downloaded
    for f in small.files:  # as a finished download leaves them
        where.mkdir(exist_ok=True)
        with (where / f.name).open("wb") as out:
            out.truncate(f.size)
    assert llm.current(where) == small
    llm.Choice(big.id, True).save(where)  # a bigger one chosen, not here yet: the small one stays
    assert llm.current(where) == small
    llm.Choice(small.id, False).save(where)
    assert llm.current(where) is None  # turned off
    llm.Choice(small.id, True).save(where)
    monkeypatch.setattr(llm, "memory_gb", lambda: 8.0)
    assert llm.current(where) is None and not llm.enough_memory()


def test_settings_offers_the_model(state: AppState, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, F811
    monkeypatch.setenv("TAXBINDER_MODELS", str(tmp_path / "models"))
    assert {"/api/reading/download", "/api/reading/delete", "/api/reading/use"} <= LOCAL_ONLY
    c = client(state)
    page = c.get("/settings").text
    assert "Reading with an AI model" in page, page[page.find("<main"):][:400]
    assert "Download 3.0 GB" in page and "Nothing about your documents" in page
    r = c.post("/api/reading/use", json={"model": "qwen3-vl-8b-q4"}, headers=ORIGIN)
    assert r.status_code == 400 and "download it first" in r.text
    assert c.post("/api/reading/use", json={"on": False}, headers=ORIGIN).json()["use"] is False
    monkeypatch.setattr(llm, "memory_gb", lambda: 8.0)
    page = c.get("/settings").text
    assert "needs 16 GB" in page and "Download 3.0 GB" not in page
    r = c.post("/api/reading/download", json={"model": "qwen3-vl-4b-q4"}, headers=ORIGIN)
    assert r.status_code == 400  # nothing is downloaded where it can't be used


def test_a_small_computer_is_told_at_the_start(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Start says reading is with the rules alone under 16 GB."""
    from fastapi.testclient import TestClient
    from test_web import BASE, PORT

    from taxbinder.web.app import create_app

    s = AppState(vault_path=tmp_path / "vault", problem="none yet", problem_kind="new", port=PORT)
    c = TestClient(create_app(s), base_url=BASE)
    c.get(f"/bootstrap?t={s.bootstrap_code}")
    monkeypatch.setattr(llm, "memory_gb", lambda: 8.0)
    assert "with its rules alone" in c.get("/").text
    monkeypatch.setattr(llm, "memory_gb", lambda: 16.0)
    assert "with its rules alone" not in c.get("/").text
