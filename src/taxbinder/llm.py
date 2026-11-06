"""The model that reads forms with the rules: which models there are, the one in
use, its files on this computer, downloading them, and a newer list from 4Ampersand.

- **The list** (`reading_models.json`, part of the app): each model's files, pinned to one
  revision, with their size and SHA-256, its licence and the memory it needs, best first. A
  newer list is taken only with 4Ampersand's signature (Ed25519, `PUBLIC_KEY`; the private key
  is kept outside the repository, `taxbinder.dev.model_list`).
- **Files** live in the models folder beside the binder (`folder`), never in the binder or its
  backups. A download is asked for by the person (its size shown), resumes after an
  interruption, is checked against its checksum before it's used, and only then replaces
  anything. Only `https://huggingface.co/` addresses are fetched (its own download servers
  after that); nothing about the person or their documents is sent.
- **The choice** (`choice.json`): the model in use and whether reading with it is on. A newer
  model is offered; the current one stays until the new one is downloaded and checked.

Values from documents never come here. Downloads are logged by file name and size only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUILT_IN = Path(__file__).with_name("reading_models.json")
# 4Ampersand's key for newer model lists (Ed25519, hex); the private half: the maintainer's Keychain
PUBLIC_KEY: str | None = "3164bc1929c5852a72e85a9890af857a5fcf8e13e1921df0d0f389ea19c23f15"
UPDATE_URL = "https://github.com/4ampersand/taxbinder/releases/latest/download/reading_models.json"
ALLOWED = {"huggingface.co"}  # where a model file's address may point (then its CDN)
MIN_MEMORY_GB = 16  # less: the app works without the model, and says so
CHUNK = 1 << 20


@dataclass(frozen=True)
class File:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    maker: str
    licence: str
    page: str
    min_memory_gb: float
    model: File
    mmproj: File | None

    @property
    def files(self) -> list[File]:
        return [self.model, *([self.mmproj] if self.mmproj else [])]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def parse(data: dict[str, Any]) -> list[Model]:
    """A model list (`reading_models.json`'s shape); ValueError if it isn't one."""
    out = []
    try:
        for m in data["models"]:
            files = {k: File(**v) for k, v in m["files"].items()}
            for f in files.values():
                url = urllib.parse.urlparse(f.url)
                if url.scheme != "https" or url.hostname not in ALLOWED or "/" in f.name:
                    raise ValueError(f"{m['id']}: a file from somewhere else")
                if len(f.sha256) != 64 or f.size <= 0:
                    raise ValueError(f"{m['id']}: no checksum or size")
            memory = float(m["min_memory_gb"])
            out.append(Model(m["id"], m["name"], m["maker"], m["licence"], m["page"], memory,
                             files["model"], files.get("mmproj")))  # fmt: skip
    except (KeyError, TypeError) as e:
        raise ValueError("not an AI model list") from e
    return out


def folder(vault_path: Path) -> Path:
    """The models folder: beside the binder (not in it, so never backed up or shared);
    `TAXBINDER_MODELS` names another."""
    if env := os.environ.get("TAXBINDER_MODELS"):
        return Path(env).expanduser()
    return vault_path.parent / "models"


def memory_gb() -> float:
    """This computer's memory (GB)."""
    if sys.platform == "win32":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                        ("rest", ctypes.c_ulonglong * 5)]  # fmt: skip

        s = Status()
        s.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))  # type: ignore[attr-defined]
        return s.total / 2**30
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def enough_memory() -> bool:
    return memory_gb() >= MIN_MEMORY_GB - 0.5  # "16 GB" Macs report a little less


def catalogue(where: Path, key: str | None = PUBLIC_KEY) -> list[Model]:
    """The models there are: a newer list fetched before (`check`), its signature checked again,
    else the app's own."""
    saved, signature = where / "list.json", where / "list.json.sig"
    if saved.is_file() and signature.is_file():
        data = saved.read_bytes()
        try:
            if verify(data, signature.read_bytes(), key):
                return parse(json.loads(data))
        except ValueError:
            pass
        log.warning("the saved model list isn't signed or valid: the app's own is used")
    return parse(json.loads(BUILT_IN.read_text()))


@dataclass
class Choice:
    model: str | None = None  # the model in use (its id); None: the first one that fits
    use: bool = True  # reading with the model, when it's downloaded

    @staticmethod
    def load(where: Path) -> Choice:
        try:
            data = json.loads((where / "choice.json").read_text())
            return Choice(data.get("model"), bool(data.get("use", True)))
        except (OSError, ValueError):
            return Choice()

    def save(self, where: Path) -> None:
        where.mkdir(parents=True, exist_ok=True)
        tmp = where / "choice.json.tmp"
        tmp.write_text(json.dumps({"model": self.model, "use": self.use}))
        tmp.replace(where / "choice.json")


def suggested(models: list[Model], memory: float | None = None) -> Model | None:
    """The best model this computer can run (the list is best first)."""
    memory = memory_gb() if memory is None else memory
    return next((m for m in models if m.min_memory_gb - 0.5 <= memory), None)


def installed(where: Path, model: Model) -> bool:
    """Its files are here and complete (each was checked against its checksum when it came)."""
    return all((where / f.name).is_file() and (where / f.name).stat().st_size == f.size
               for f in model.files)  # fmt: skip


def current(where: Path) -> Model | None:
    """The model reading uses now: the chosen one if it's here; None when reading with the
    model is off, nothing is downloaded, or this computer has too little memory."""
    choice = Choice.load(where)
    if not choice.use or not enough_memory():
        return None
    models = catalogue(where)
    chosen = next((m for m in models if m.id == choice.model), None) or suggested(models)
    if chosen is not None and installed(where, chosen):
        return chosen
    return next((m for m in models if installed(where, m)), None)  # an older one, until then


def delete(where: Path, model: Model) -> None:
    for f in model.files:
        for p in (where / f.name, where / (f.name + ".part")):
            p.unlink(missing_ok=True)


# -- downloads --------------------------------------------------------------------------------


@dataclass
class Download:
    """One model's files coming in the background; `progress` for the page."""

    model: Model
    where: Path
    done: int = 0
    error: str = ""
    finished: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    @property
    def progress(self) -> dict[str, Any]:
        return {"model": self.model.id, "done": self.done, "size": self.model.size,
                "finished": self.finished, "error": self.error}  # fmt: skip

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="model-download", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            free = shutil.disk_usage(self.where if self.where.exists() else self.where.parent).free
            needed = sum(f.size for f in self.model.files if not _complete(self.where, f))
            if needed > free:
                raise OSError(f"not enough room: {needed / 1e9:.1f} GB needed, "
                              f"{free / 1e9:.1f} GB free")  # fmt: skip
            self.where.mkdir(parents=True, exist_ok=True)
            base = 0
            for f in self.model.files:
                if not _complete(self.where, f):
                    _fetch(
                        f, self.where, self._stop, lambda n, b=base: setattr(self, "done", b + n)
                    )
                base += f.size
                self.done = base
            if not self._stop.is_set():
                log.info("model %s downloaded (%.1f GB)", self.model.id, self.model.size / 1e9)
        except (OSError, ValueError) as e:
            self.error = str(e) if not isinstance(e, urllib.error.URLError) else "no connection"
            log.warning("model download stopped: %s", self.error)
        finally:
            self.finished = True


def _complete(where: Path, f: File) -> bool:
    p = where / f.name
    return p.is_file() and p.stat().st_size == f.size


def _fetch(f: File, where: Path, stop: threading.Event, seen: Any) -> None:  # noqa: ANN401
    """One file into `<name>.part`, from where an earlier attempt stopped; checked, then put in
    place. A file that doesn't match its checksum is thrown away."""
    part = where / (f.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > f.size:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with part.open("rb") as old:
            while chunk := old.read(CHUNK):
                digest.update(chunk)
    if have < f.size:
        headers = {"User-Agent": "TaxBinder"} | ({"Range": f"bytes={have}-"} if have else {})
        with _open(urllib.request.Request(f.url, headers=headers)) as resp:
            if have and resp.status != 206:  # the server sent it all again: start over
                have, digest = 0, hashlib.sha256()
                part.unlink(missing_ok=True)
            with part.open("ab") as out:
                while not stop.is_set() and (chunk := resp.read(CHUNK)):
                    out.write(chunk)
                    digest.update(chunk)
                    have += len(chunk)
                    seen(have)
    if stop.is_set():
        return  # kept, to go on from later
    if have != f.size or digest.hexdigest() != f.sha256:
        part.unlink(missing_ok=True)
        raise ValueError(f"{f.name} didn't arrive intact: it was thrown away")
    part.replace(where / f.name)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise urllib.error.URLError("a download was redirected away from https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req: urllib.request.Request, timeout: float = 60) -> Any:  # noqa: ANN401
    if urllib.parse.urlparse(req.full_url).hostname not in ALLOWED | {"github.com"}:
        raise ValueError("not an address models come from")
    return urllib.request.build_opener(_HttpsOnly).open(req, timeout=timeout)


# -- a newer list -----------------------------------------------------------------------------


def verify(data: bytes, signature: bytes, key: str | None = PUBLIC_KEY) -> bool:
    """Is `data` signed with 4Ampersand's key?"""
    if not key:
        return False
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature, data)
    except (InvalidSignature, ValueError):
        return False
    return True


def check(where: Path, url: str = UPDATE_URL, key: str | None = PUBLIC_KEY) -> list[Model]:
    """Fetch 4Ampersand's latest model list; keep it only if its signature is right. Returns the
    models now listed (the app's own when there's nothing newer)."""
    if not key:
        raise ValueError("this copy of TaxBinder can't check for newer models yet")
    with _open(urllib.request.Request(url, headers={"User-Agent": "TaxBinder"}), 30) as resp:
        data = resp.read(1 << 20)
    with _open(urllib.request.Request(url + ".sig", headers={"User-Agent": "TaxBinder"}), 30) as r:
        signature = r.read(1024)
    if not verify(data, signature, key):
        raise ValueError("the AI model list isn't signed by 4Ampersand: not used")
    parse(json.loads(data))  # a list it can use
    if json.loads(data).get("version", 0) >= json.loads(BUILT_IN.read_text()).get("version", 0):
        where.mkdir(parents=True, exist_ok=True)
        (where / "list.json.sig").write_bytes(signature)
        (where / "list.json").write_bytes(data)
    return catalogue(where, key)
