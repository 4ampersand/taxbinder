"""A newer TaxBinder: which version GitHub's latest release is, so Settings can say so. The
person downloads it themselves; nothing is installed from here.

- **The check** asks `LATEST`, which GitHub redirects to the latest release's page
  (`…/releases/tag/v0.11.0`); the version is read from that address, which isn't followed.
  Nothing about the person or their documents is sent, and only `https://github.com/` is asked.
- **When**: when the app opens, at most once a day, unless the person turned it off; or when
  they press Check now. With no connection nothing is shown, and it's tried at the next launch.
- **What's kept** (`updates.json`, one per computer, beside the binder like the AI models): the
  on/off choice, when it last checked, and the newest version it saw.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LATEST = "https://github.com/4ampersand/taxbinder/releases/latest"
DAY = 24 * 3600
TAG = re.compile(r"/releases/tag/v?(\d+(?:\.\d+){1,3})$")


def version_key(v: str) -> tuple[int, ...] | None:
    """`"0.10.1"` → (0, 10, 1); None for anything that isn't a plain version."""
    return tuple(int(n) for n in v.split(".")) if re.fullmatch(r"\d+(?:\.\d+){1,3}", v) else None


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None  # not followed: the redirect's address is the answer


def _open(req: urllib.request.Request, timeout: float):  # noqa: ANN202 - tests swap it
    return urllib.request.build_opener(_Stay).open(req, timeout=timeout)


def newest(timeout: float = 15) -> str | None:
    """The latest release's version, from the address GitHub redirects its page to."""
    req = urllib.request.Request(LATEST, method="HEAD", headers={"User-Agent": "TaxBinder"})
    try:
        _open(req, timeout).close()
        return None  # no redirect: no release yet
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        to = urllib.parse.urlparse(urllib.parse.urljoin(LATEST, e.headers.get("Location", "")))
    if to.scheme != "https" or to.hostname != "github.com":
        return None
    m = TAG.search(to.path)
    return m.group(1) if m else None


@dataclass
class Updates:
    check: bool = True  # look when the app opens (Settings › About)
    checked: float = 0  # when it last looked (seconds since 1970)
    newest: str | None = None  # the latest release it saw

    @staticmethod
    def load(where: Path) -> Updates:
        try:
            data = json.loads((where / "updates.json").read_text())
            newest = data.get("newest")
            if not (isinstance(newest, str) and version_key(newest)):
                newest = None  # never shown unless it's a version
            return Updates(bool(data.get("check", True)), float(data.get("checked", 0)), newest)
        except (OSError, ValueError, TypeError, AttributeError):
            return Updates()

    def save(self, where: Path) -> None:
        where.mkdir(parents=True, exist_ok=True)
        tmp = where / "updates.json.tmp"
        tmp.write_text(json.dumps(asdict(self)))
        tmp.replace(where / "updates.json")

    def due(self, now: float) -> bool:
        return self.check and not 0 <= now - self.checked < DAY

    def newer(self, current: str) -> str | None:
        """The newest version seen, if it's newer than `current`."""
        mine, theirs = version_key(current), self.newest and version_key(self.newest)
        return self.newest if mine and theirs and theirs > mine else None


def look(where: Path, now: float | None = None) -> Updates:
    """Ask GitHub now and keep the answer. Raises OSError with no connection (nothing kept)."""
    found = newest()
    u = Updates.load(where)
    u.checked, u.newest = time.time() if now is None else now, found or u.newest
    u.save(where)
    return u


def at_launch(where: Path, now: float | None = None) -> None:
    """The once-a-day look when the app opens: quiet when off, not due, or offline."""
    now = time.time() if now is None else now
    if not Updates.load(where).due(now):
        return
    try:
        u = look(where, now)
        log.info("updates: the latest release is %s", u.newest)
    except OSError as e:  # no connection: tried again next launch
        log.info("updates: not checked (%s)", type(e).__name__)
