"""Desktop entry point: the backend the Tauri shell starts as a sidecar (§11.4).

Startup contract with the shell:
  1. Bind 127.0.0.1 on an OS-chosen port and start listening.
  2. Print one control line:  @@TAXBINDER {"type":"ready","port":N,"bootstrap":"..."}
  3. Serve until stdin reaches EOF (the shell exited or crashed) or a signal arrives.

For development without the shell:
    uv run python -m taxbinder.sidecar --vault ~/.taxbinder-dev/vault --no-stdin-watchdog
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("taxbinder")


def watch_stdin_then_exit(lock: threading.Lock, before: Callable[[], None] = lambda: None) -> None:
    """Exit when the parent closes our stdin, so a crashed shell never leaves an orphan.

    `before` runs first (the reading engine is stopped: it's a process of its own). Waits
    (briefly) for any vault operation in progress, so an exit never lands in the middle of a
    write or a git commit.
    """
    stream = sys.stdin.buffer if sys.stdin else None
    if stream is None:
        return
    try:
        while stream.read(1024):
            pass
    except (OSError, ValueError):
        pass
    log.info("stdin closed; parent gone, exiting")
    try:
        before()
    except Exception:  # noqa: BLE001 - exiting regardless
        log.warning("couldn't stop the reading engine")
    lock.acquire(timeout=10)
    os._exit(0)


def keep_issuer_tins(state) -> None:  # noqa: ANN001 - an AppState
    """Documents added before each kept its issuer's EIN get it, in the background."""
    from taxbinder.ingest.inbox import fill_issuer_tins

    with state.lock:
        stores = [s for s in (state.store, *state._entity_stores.values()) if s]
    for store in stores:
        try:
            fill_issuer_tins(store, "auto:ingest", state.lock)
        except Exception as e:  # noqa: BLE001 - tried again next start
            log.warning("issuers' EINs not kept (%s)", type(e).__name__)


def open_state(vault_path: Path):  # noqa: ANN201
    """AppState with the vault's store, or with the reason it couldn't be opened."""
    from taxbinder.web.app import AppState

    state = AppState(vault_path=vault_path)
    if downloads := os.environ.get("TAXBINDER_DOWNLOADS"):  # test runs: never the real ~/Downloads
        state.downloads_dir = Path(downloads)
    state.open()
    return state


def main(argv: list[str] | None = None) -> int:
    from taxbinder.store import default_vault_path

    ap = argparse.ArgumentParser(prog="taxbinder-backend")
    ap.add_argument("--vault", type=Path, default=default_vault_path())
    ap.add_argument("--port", type=int, default=0, help="0 = let the OS choose (default)")
    ap.add_argument("--no-stdin-watchdog", action="store_true", help="for running from a terminal")
    ap.add_argument("--log-level", default=os.environ.get("TAXBINDER_LOG", "info"))
    args = ap.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,  # stdout is reserved for control messages
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    from taxbinder import perf

    log.info("performance log: %s", perf.setup())  # a file of its own (perf.py)

    import uvicorn

    from taxbinder.web.app import create_app
    from taxbinder.web.control import emit
    from taxbinder.web.watcher import InboxWatcher, process_inbox

    state = open_state(args.vault.expanduser())
    state.in_app = not args.no_stdin_watchdog  # started by the shell, which reads stdout
    app = create_app(state)
    from taxbinder import ocr

    ocr.warm_up()  # text recognition loads its models once, in the background
    from taxbinder.modelread import ModelReader

    state.model_reader = ModelReader(state)  # documents read with the model, when there is one
    state.model_reader.start()
    tins = threading.Thread(target=keep_issuer_tins, args=(state,), name="issuer-tins", daemon=True)
    tins.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)  # listen before announcing, so the shell's first request queues
    state.port = sock.getsockname()[1]

    watcher = InboxWatcher(state)  # waits while there's no vault (Start creates or restores it)
    process_inbox(state)  # anything left from a previous run
    watcher.start()
    app.state.reader.wake()  # Add documents: files queued when the app last quit
    if not args.no_stdin_watchdog:
        threading.Thread(
            target=watch_stdin_then_exit,
            args=(state.lock, state.model_reader.stop),
            name="stdin-watchdog",
            daemon=True,
        ).start()

    def on_signal(signum: int, _frame: object) -> None:
        # uvicorn hands a stop signal on to this handler once it has shut down: stop the
        # reading engine and leave through `finally` below, never killed mid-way
        state.model_reader.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log.info("vault=%s port=%d ready=%s", state.vault_path, state.port, state.store is not None)
    emit({"type": "ready", "port": state.port, "bootstrap": state.bootstrap_code})
    if args.no_stdin_watchdog:
        url = f"http://127.0.0.1:{state.port}/bootstrap?t={state.bootstrap_code}"
        print(f"Open {url}", file=sys.stderr, flush=True)

    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
    try:
        server.run(sockets=[sock])
    finally:
        watcher.stop()
        app.state.reader.stop()
        state.model_reader.stop()  # and its engine, which holds the model's memory
        if state.sharing is not None:  # the network listener goes with the app
            state.sharing.stop()
        state.close_all()  # entity vaults (the household's is closed below)
        if state.store is not None:
            with state.lock:
                state.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
