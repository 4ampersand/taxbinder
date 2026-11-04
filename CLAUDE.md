# TaxBinder: notes for Claude Code

A macOS desktop app that keeps a household's tax documents in order: what to expect each year,
reading what arrives, checking it, and the files for the preparer. What it is:
[docs/product.md](docs/product.md). How it's built: [docs/architecture.md](docs/architecture.md)
(the code cites its sections as "§4.1"). How to work on it, including the rules for record shapes
and vault formats: [CONTRIBUTING.md](CONTRIBUTING.md). Read the architecture before a change
that crosses modules.

## How to work

- Small, readable changes in the style of the surrounding code; type hints; `ruff` clean.
- Each piece of work ends with a passing `make test`; commit only when asked.
- Every write goes through `act()` in `web/app.py` and the shared modules; writes are undoable.
- No AI service on the internet, ever. The one model is local (`engine.py`, `llm.py`) and the
  rules check what it reads; every other decision is a deterministic rule.
- After changing a model: `uv run taxbinder schema export`. After changing a command's help:
  `make cli-docs`. Both are checked by tests.

## Easy to get wrong

- Sharing (`web/sharing.py`): the guard tells network requests apart by the
  `sharing.LAN_SCOPE_KEY` label, never by port. Anything that drives this Mac or manages sharing
  goes in `LOCAL_ONLY` in `web/app.py`.
- Whose a document is (`whose.py`): unclear → asked, never guessed. Documents never move to
  another filer's vault by themselves.
- Tax returns (`returns.py`) are recognised by their printed titles, never a form number; a
  1040's totals never become an invented issuer.
- Papers for the preparer (`preparer.py`) are never typed, matched, read or on Check.
- OCR (`ocr.py`): no Apple-only technology; its models are bundled by the build, never
  downloaded by the app. Reading slips (`slack.py`) are allowed only when comparing with known
  values, never for amounts.
- States (`states.py`, `rules/states.yaml`): unreviewed dates are labelled wherever they show;
  dates in `rules/params` win.
- The demo (`demo.py`): its own folder and key file; never backed up, shared or in the Keychain.
  Its sample files are made at build time (`make samples`), never by the app.

## Data safety (non-negotiable)

- **Nothing personal in this repository, ever**: no real tax data, PDFs, vaults, names, account
  numbers, amounts or dates from a real household, in code, tests, docs or commit messages.
  Tests use the invented household (`tests/quills.py`, `tests/samples/`) and generated PDFs.
- Never print, log or write to fixtures: account numbers, SSNs/EINs, cookie values, or URL query
  strings (they can hold one-time tokens). Mask as `…1234`.
- Development uses a throwaway vault: `export TAXBINDER_VAULT=~/.taxbinder-dev/vault`. The real
  vault is `~/Library/Application Support/com.4ampersand.taxbinder/vault`; never touch it from
  tests or scripts. Without `TAXBINDER_VAULT`, `make dev` and the CLI open the real vault.
- `make privacy` and `uv run python -m taxbinder.dev.reading_check` read a temporary copy of the
  maintainer's real vault. Run them only when the maintainer asks; test them on fixture vaults.
- Tests use `TAXBINDER_KEYSTORE=memory` (enforced by `tests/conftest.py`).
- Click-throughs of the frozen app: set `TAXBINDER_DOWNLOADS` to a scratch folder, so nothing is
  saved into the real ~/Downloads.
- A new secret field goes into `ENCRYPTED_FIELDS` in `store/crypto.py`.
- Redaction (`redact/`): a leftover known value is an error (nothing is produced). Never log or
  print redacted-away values.
- PyMuPDF is for generating test PDFs only; never import it from `src/`.
- Rule parameter files marked `reviewed_by` were checked by a person; don't change their values
  without saying so.

## Commands

```bash
make setup      # uv sync + the Tauri CLI + the reading engine
make test       # ruff + pytest + cargo test
make backend    # the backend alone; open the printed URL in a browser
make dev        # the desktop app on $TAXBINDER_VAULT (rebuilds the frozen backend)
make cli-docs   # docs/cli.md from the commands' help
uv run taxbinder --help        # every command: docs/cli.md
uv run python -m tests.quills  # the invented household's 2025 into $TAXBINDER_VAULT (after init)
```
