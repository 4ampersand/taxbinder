"""The reading engine's lifecycle with a stand-in for `llama-server`: started on demand on
127.0.0.1 with its own key (in its environment), offline, no web page, none of the LLAMA_
settings passed on; stopped when done, when idle, and when a previous run left one behind. A
real engine and model are checked by hand, never in the tests."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from taxbinder import engine
from taxbinder.engine import Engine, EngineError

STAND_IN = '''#!{python}
import json, sys, os, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1]
open({record!r}, "w").write(json.dumps({{"args": args, "env": [k for k in os.environ if k.startswith("LLAMA_")]}}))
started = time.monotonic()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _authorised(self):
        return self.headers.get("Authorization") == "Bearer " + os.environ["LLAMA_API_KEY"]
    def do_GET(self):
        if not self._authorised(): return self._send(401, {{}})
        if time.monotonic() - started < 0.6: return self._send(503, {{"status": "loading"}})
        self._send(200, {{"status": "ok"}})
    def do_POST(self):
        if not self._authorised(): return self._send(401, {{}})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        answer = json.dumps({{"seen": body["messages"][0]["content"], "t": body["temperature"],
                             "schema": "response_format" in body}})
        self._send(200, {{"choices": [{{"message": {{"content": answer}}}}]}})
HTTPServer((opt("--host"), int(opt("--port"))), H).serve_forever()
'''


@pytest.fixture
def stand_in(tmp_path: Path) -> tuple[Path, Path, Path]:
    server, record = tmp_path / "llama-server", tmp_path / "record.json"
    server.write_text(STAND_IN.format(python=sys.executable, record=str(record)))
    server.chmod(server.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    return server, model, record


def test_started_on_demand_locally_with_its_own_key(stand_in, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    server, model, record = stand_in
    monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")  # never passed on
    with Engine(model, server=server, pidfile=tmp_path / "engine.pid") as e:
        assert not e.running
        answer = json.loads(e.ask([{"role": "user", "content": "box 1?"}], {"type": "object"}))
        assert answer == {"seen": "box 1?", "t": 0, "schema": True}
        assert e.running and (tmp_path / "engine.pid").is_file()
        seen = json.loads(record.read_text())
        args = seen["args"]
        assert args[args.index("--host") + 1] == "127.0.0.1"
        assert {"--offline", "--no-webui", "--log-disable"} <= set(args)
        assert seen["env"] == ["LLAMA_API_KEY"] and "--api-key" not in args  # not in `ps`
        port = args[args.index("--port") + 1]
        with pytest.raises(urllib.error.HTTPError) as no_key:  # the key is needed
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
        assert no_key.value.code == 401
        proc = e._proc
    assert not e.running and proc.poll() is not None
    assert not (tmp_path / "engine.pid").exists()


def test_stopped_when_idle(stand_in) -> None:  # noqa: ANN001
    server, model, _ = stand_in
    e = Engine(model, server=server, idle=0.5)
    e.start()
    deadline = time.monotonic() + 15
    while e.running and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not e.running


def test_one_left_behind_is_stopped(stand_in, tmp_path) -> None:  # noqa: ANN001
    server, model, _ = stand_in
    pidfile = tmp_path / "engine.pid"
    first = Engine(model, server=server, pidfile=pidfile)
    first.start()
    old = first._proc
    first._proc = None  # as if the app had stopped abruptly: the engine and its pidfile remain
    try:
        with Engine(model, server=server, pidfile=pidfile) as again:
            again.start()
            old.wait(10)  # the one left behind was stopped
            assert again.running
    finally:
        if old.poll() is None:
            old.kill()


def test_what_stops_it_from_starting(stand_in, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    server, model, _ = stand_in
    with pytest.raises(EngineError, match="isn't downloaded"):
        Engine(tmp_path / "missing.gguf", server=server).start()
    monkeypatch.setenv("TAXBINDER_ENGINE", str(tmp_path / "none"))
    assert engine.server_path() is None
    with pytest.raises(EngineError, match="isn't part of this copy"):
        Engine(model).start()
    crash = tmp_path / "crash"
    crash.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(3)\n")
    crash.chmod(0o755)
    with pytest.raises(EngineError, match="stopped while loading"):
        Engine(model, server=crash).start(timeout=10)


@pytest.mark.skipif(sys.platform != "darwin" or not engine.server_path(), reason="no engine fetched")
def test_the_fetched_engine_runs() -> None:
    """`make engine`'s llama-server starts (its libraries found beside it)."""
    out = subprocess.run([str(engine.server_path()), "--version"], capture_output=True,
                         text=True, timeout=30, env={**os.environ})  # fmt: skip
    assert "version:" in out.stdout + out.stderr
