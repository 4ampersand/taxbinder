"""The reading engine: llama.cpp's `llama-server` (MIT), bundled with the app and
signed with it. The backend starts it only while documents are being read with the model and
stops it after (a model holds gigabytes of memory), or when the app quits.

It listens on 127.0.0.1 only, on a port chosen at each start, and answers only requests that
carry the key made for that start (given in its environment, which other users' processes can't
read, never on its command line, which they can). It's told to work offline, without its web
page or tools, and none of the other `LLAMA_*` settings from the environment reach it. Its
output is discarded: it could hold a document's text, which is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NAME = "llama-server.exe" if sys.platform == "win32" else "llama-server"
ROOT = Path(__file__).resolve().parents[2]  # the repository, when not frozen
CONTEXT = 16384  # tokens: a form's page image and its text, the boxes asked for, the answer
STARTUP = 300  # seconds to load a model (several GB from disk) before giving up
IDLE = 300  # seconds without a request before the engine is stopped, to free its memory


class EngineError(RuntimeError):
    """The engine didn't start, or stopped."""


class Unanswered(EngineError):
    """One request wasn't answered (too long, refused): the engine itself is fine."""


def server_path() -> Path | None:
    """The bundled `llama-server`: beside the frozen backend, else (development) the one
    `make engine` fetched; `TAXBINDER_ENGINE` names another."""
    if env := os.environ.get("TAXBINDER_ENGINE"):
        return Path(env) if Path(env).is_file() else None
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).parent / "engine" / NAME
    else:
        here = ROOT / "build" / "engine" / NAME
    return here if here.is_file() else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Engine:
    """One model loaded in one `llama-server`. `ask` starts it when needed; it stops by itself
    after `idle` seconds without a request, or with `stop`."""

    def __init__(
        self,
        model: Path,
        mmproj: Path | None = None,
        server: Path | None = None,
        pidfile: Path | None = None,
        idle: float = IDLE,
    ) -> None:
        self.model, self.mmproj = model, mmproj
        self.server = server or server_path()
        self.pidfile = pidfile  # so a run that ended abruptly doesn't leave one behind
        self.idle = idle
        self._proc: subprocess.Popen | None = None
        self._port, self._key = 0, ""
        self._lock = threading.Lock()
        self._last = 0.0
        self._timer: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = STARTUP) -> None:
        with self._lock:
            if self.running:
                return
            if self.server is None:
                raise EngineError("the reading engine isn't part of this copy of TaxBinder")
            if not self.model.is_file() or (self.mmproj and not self.mmproj.is_file()):
                raise EngineError("the AI model isn't downloaded")
            _stop_left_over(self.pidfile)
            self._port, self._key = _free_port(), secrets.token_urlsafe(24)
            cmd = [str(self.server), "--model", str(self.model), "--host", "127.0.0.1",
                   "--port", str(self._port), "--ctx-size", str(CONTEXT), "--parallel", "1",
                   "--offline", "--no-webui", "--log-disable"]  # fmt: skip
            if self.mmproj:
                cmd += ["--mmproj", str(self.mmproj)]
            env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
            env["LLAMA_API_KEY"] = self._key
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,  # its own group: stopped with it, never by the user's ^C
            )
            if self.pidfile:
                self.pidfile.parent.mkdir(parents=True, exist_ok=True)
                self.pidfile.write_text(f"{self._proc.pid}\n{self.server}\n")
            log.info("reading engine starting (port %d)", self._port)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                self.stop()
                raise EngineError("the reading engine stopped while loading the AI model")
            try:
                if self._get("/health").get("status") == "ok":
                    self._touch()
                    log.info("reading engine ready")
                    return
            except (OSError, ValueError):
                pass  # still loading (503) or not listening yet
            time.sleep(0.5)
        self.stop()
        raise EngineError("the reading engine didn't load the AI model in time")

    def ask(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        max_tokens: int = 4000,
        timeout: float = 300,
    ) -> str:
        """The model's answer (temperature 0, a fixed seed); with `schema`, JSON that follows it."""
        self.start()
        body: dict[str, Any] = {"messages": messages, "temperature": 0, "seed": 1,
                                "max_tokens": max_tokens}  # fmt: skip
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": schema},
            }
        self._touch()
        try:
            reply = self._post("/v1/chat/completions", body, timeout)
        finally:
            self._touch()
        message = reply["choices"][0]["message"]
        return message.get("content") or message.get("reasoning_content") or ""

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)
                log.info("reading engine stopped")
            if self.pidfile and self.pidfile.exists():
                self.pidfile.unlink()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # -- inside --
    def _touch(self) -> None:
        self._last = time.monotonic()
        if self._timer is None or not self._timer.is_alive():
            self._timer = threading.Thread(target=self._idle_stop, name="engine-idle", daemon=True)
            self._timer.start()

    def _idle_stop(self) -> None:
        while self.running:
            time.sleep(min(5.0, self.idle))
            if time.monotonic() - self._last > self.idle:
                self.stop()

    def _request(self, path: str, data: bytes | None, timeout: float) -> dict[str, Any]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{path}",
            data,
            {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if path == "/health":
                raise ValueError("loading") from e
            raise Unanswered(f"the reading engine refused the request ({e.code})") from e

    def _get(self, path: str) -> dict[str, Any]:
        return self._request(path, None, 5)

    def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            return self._request(path, json.dumps(body).encode(), timeout)
        except TimeoutError as e:
            raise Unanswered("the reading engine didn't answer in time") from e
        except OSError as e:
            raise EngineError("the reading engine isn't answering") from e


def _stop_left_over(pidfile: Path | None) -> None:
    """An engine left running when the app last stopped abruptly: stopped (only if that
    process is still the engine)."""
    if pidfile is None or not pidfile.is_file():
        return
    try:
        pid_text, path = pidfile.read_text().splitlines()[:2]
        pid = int(pid_text)
    except ValueError:
        pidfile.unlink()
        return
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                             text=True, timeout=5).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        out = ""
    if path in out:  # still the engine, not another program that got the number since
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    pidfile.unlink()
