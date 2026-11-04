"""Control channel from the backend to the desktop shell (§11.4).

Lines on stdout that start with CONTROL_PREFIX are JSON messages for the shell; logs go to stderr.

    {"type": "ready", "port": 51234, "bootstrap": "<one-time code>"}
    {"type": "open_portal", "url": "https://...", "label": "optional title"}
    {"type": "capture_page"}
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

CONTROL_PREFIX = "@@TAXBINDER "
Emitter = Callable[[dict[str, Any]], None]

_lock = threading.Lock()


def emit(msg: dict[str, Any], stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    with _lock:
        out.write(CONTROL_PREFIX + json.dumps(msg, separators=(",", ":")) + "\n")
        out.flush()
