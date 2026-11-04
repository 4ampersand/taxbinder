"""The sidecar as the shell runs it: ready line, portal download ingested, exit on stdin EOF."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx
import pytest
from pdfgen import make_pdf
from quills import build

from taxbinder.store import FileKeys, RecordStore, init_vault, open_vault
from taxbinder.web.control import CONTROL_PREFIX


@pytest.fixture
def vault_and_keys(tmp_path: Path):
    keys = FileKeys(tmp_path / "keys.json")
    vault, _ = init_vault(tmp_path / "vault", keys)
    with RecordStore(vault) as store:
        build(store, "test")
    return vault, keys


def test_sidecar_lifecycle(vault_and_keys) -> None:
    vault, keys = vault_and_keys
    env = {**os.environ, "TAXBINDER_KEYSTORE": f"file:{keys.path}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "taxbinder.sidecar", "--vault", str(vault.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        line = proc.stdout.readline().decode()
        assert line.startswith(CONTROL_PREFIX), line
        ready = json.loads(line[len(CONTROL_PREFIX) :])
        assert ready["type"] == "ready" and ready["bootstrap"]
        base = f"http://127.0.0.1:{ready['port']}"
        assert httpx.get(f"{base}/healthz").json() == {"ok": True}

        with httpx.Client(base_url=base) as web:
            assert web.get(f"/bootstrap?t={ready['bootstrap']}").status_code == 200
            assert "Your binder" in web.get("/").text  # your binder, home

        # A portal download: the shell writes it into inbox/portal with a .meta.json.
        pdf = vault.inbox / "portal" / "Corvina K-1 2025.pdf"
        pdf.write_bytes(make_pdf(["Schedule K-1 (Form 1065) 2025", "Corvina Ledger Fund"]))
        (vault.inbox / "portal" / "Corvina K-1 2025.pdf.meta.json").write_text(
            json.dumps({"source_url": "https://portal.example.com/k1?sig=abc"})
        )
        findings = vault.records / "years" / "2025" / "findings.yaml"
        deadline = time.time() + 20
        while (pdf.exists() or not findings.exists()) and time.time() < deadline:
            time.sleep(0.3)
        assert not pdf.exists(), "watcher did not ingest the download"
        assert findings.exists(), "watcher did not reconcile after ingesting"
    finally:
        proc.stdin.close()  # the shell went away
        proc.wait(timeout=10)
    assert proc.returncode == 0
    stderr = proc.stderr.read().decode()
    assert "sig=abc" not in stderr and "Blockchain" not in stderr

    with RecordStore(open_vault(vault.root, keys)) as store:
        exp, _ = store.get("expectation", "2025/copperfield-collective.corvina-ledger-fund.k1")
        assert exp.acquisition == "vaulted" and len(exp.evidence) == 1
        [doc] = store.list("document")
        assert doc.acquired.via == "portal_window"
        assert doc.acquired.source_url == "https://portal.example.com/k1"
        findings, _ = store.get("findings", "2025")  # the watcher reconciled after ingesting
        assert findings.findings
