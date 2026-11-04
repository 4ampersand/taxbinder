# Contributing to TaxBinder

Thank you for helping. Issues and pull requests are welcome. How the code is built is in
[docs/architecture.md](docs/architecture.md); what the app is for is in
[docs/product.md](docs/product.md).

## Never include real tax data

No real names, account numbers, SSNs, EINs, addresses, amounts or dates from a real household:
not in code, tests, fixtures, issues, pull requests, commit messages or screenshots. Use invented
values. The invented household in `tests/quills.py` (the same family as the sample set in
`tests/samples/`) and the PDFs the tests generate show how. Invented SSNs start with `900-` and
invented EINs with `00-`.

If you use TaxBinder with your own documents, run `make privacy` before you push. It checks the
repository, every version of every file and every commit message for anything your vault knows
(names, numbers, institutions, values you marked private) and reports each hit by place only,
never by value. It reads a copy of your vault, so quit TaxBinder first.

## Set up

You need macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/), Node.js, Rust and the Xcode
command line tools.

```bash
make setup                      # Python environment, the Tauri CLI and the reading engine
export TAXBINDER_VAULT=~/.taxbinder-dev/vault   # a throwaway vault: always set this
uv run taxbinder init
uv run python -m tests.quills   # the invented household's 2025
make backend                    # the backend alone; open the printed URL in a browser
make dev                        # or the whole app (rebuilds the frozen backend first)
```

Without `TAXBINDER_VAULT`, the app and the command line open your real vault in
`~/Library/Application Support/com.4ampersand.taxbinder/vault`. Development and scripts never
touch it.

## Make a change

- **Keep it small and readable**, in the style of the surrounding code: type hints, comments
  that say what the code does, `ruff` clean (line length 100).
- **Run `make test`**: ruff, pytest and the shell's Rust tests. It must pass. Tests keep keys in
  memory (`TAXBINDER_KEYSTORE=memory`, enforced by `tests/conftest.py`) and never use the AI
  model; a stand-in plays the reading engine.
- **Every write goes through `act()`** in `web/app.py` and the shared modules (`review.py`,
  `years.py`, …), never ad hoc in templates or scripts, so that it's one undoable change.
- **Pages** allow no inline script or style (the content security policy). Styles go in
  `web/static/`, behaviour in its scripts.
- **No AI service on the internet**, ever. The only model is the local one (`engine.py`,
  `llm.py`), and the rules check what it reads. Every other decision is a deterministic rule.
- **Identifiers are never printed or logged.** Account numbers, SSNs, EINs, cookie values and URL
  query strings (they can hold one-time tokens) stay out of logs, findings, file names and test
  output; show them as `…1234`.
- **PDFs** are read with `pdfdoc.py` (pypdfium2 and pdfplumber). PyMuPDF is only for generating
  test PDFs; never import it from `src/`.
- **Rule parameters** (`src/taxbinder/rules/params/`) marked `reviewed_by` were checked by a
  person against the official schedules. Say so in the pull request if you change their values.

### Changing a record's shape

The Pydantic models in `src/taxbinder/models/` are the single source of truth. After changing
one, run `uv run taxbinder schema export` to regenerate `schemas/` (a test checks they match).

Vaults made by earlier versions must keep opening (`tests/test_old_vaults.py`). If a change would
break them:

1. run `uv run python -m tests.fixtures.vaults.make` first, to add a fixture vault in the
   current format;
2. raise `FORMAT` in `store/vault.py`;
3. add the step from the old format to the new one in `store/upgrade.py`.

Never edit an existing fixture vault.

### A new secret field

A field holding an identifier goes into `ENCRYPTED_FIELDS` in `store/crypto.py`, so it's
written to disk as `ENC[age:…]`.

### How well it reads

`uv run python -m tests.samples.generate DIR` writes the invented sample set (forms, scans,
photos, bad files and the expected answers) into DIR, and
`uv run python -m tests.samples.score DIR` scores the reader on it.

### The command line

[docs/cli.md](docs/cli.md) is generated from the commands' own help. After changing a command,
run `make cli-docs` (a test fails until you do).

## The contributor agreement

On your first pull request a bot asks you to sign the [Contributor License Agreement](CLA.md)
with a one-line comment. You keep the copyright in your work; the agreement lets the maintainer
also offer TaxBinder under other terms, and promises your contribution stays open source.
Contributions are licensed under AGPL-3.0-or-later, the project's licence.

## Security problems

Please don't open a public issue: see [SECURITY.md](SECURITY.md).
