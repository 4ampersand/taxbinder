# TaxBinder. Requires: uv, rustup/cargo, node/npm, Xcode command line tools.

DESKTOP := desktop
TAURI   := $(DESKTOP)/src-tauri

.PHONY: setup test privacy cli-docs samples licences engine sidecar dev backend app release install clean

setup:            ## Python env + Node deps for the Tauri CLI + the reading engine
	uv sync
	cd $(DESKTOP) && npm install --silent
	uv run python $(DESKTOP)/scripts/fetch_engine.py

test:             ## ruff + pytest + shell unit tests
	uv run ruff check .
	uv run pytest -q
	mkdir -p $(TAURI)/backend  # the bundled backend's folder must exist for the shell's build
	cd $(TAURI) && cargo test --lib

privacy:          ## nothing from the real vault in this repository (reads a copy; quit TaxBinder)
	uv run python -m taxbinder.dev.privacy_check

cli-docs:         ## docs/cli.md from the command line's own help (a test checks it's current)
	TAXBINDER_VAULT=/nonexistent uv run python -m typer taxbinder.cli utils docs --name taxbinder --output docs/cli.md

samples:          ## the demo's sample files (invented household), for the app to bundle
	uv run python -m tests.samples.demo build/demo-samples

licences: engine  ## the third-party licences file, for the app to bundle
	uv run python -m taxbinder.dev.licences

engine:           ## the reading engine (llama.cpp's llama-server), checked, into build/engine
	uv run python $(DESKTOP)/scripts/fetch_engine.py

sidecar: samples engine licences  ## freeze taxbinder.sidecar into desktop/src-tauri/backend/
	uv run python $(DESKTOP)/scripts/build_sidecar.py

dev: sidecar      ## run the desktop app in dev mode (rebuilds the sidecar first)
	cd $(DESKTOP) && npx tauri dev

APP := $(TAURI)/target/release/bundle/macos/TaxBinder.app

app: sidecar      ## build TaxBinder.app (uses the real vault, like any installed app)
	cd $(DESKTOP) && npx tauri build --bundles app
	codesign --force --deep --sign - "$(APP)"  # ad-hoc, for this Mac (no Apple developer ID)
	codesign --verify --deep --strict "$(APP)"
	@echo "built $(APP)"

release:          ## signed, notarized TaxBinder.app and .dmg in build/release/
	uv run python $(DESKTOP)/scripts/release.py

install: app      ## put TaxBinder.app in /Applications (quit TaxBinder first)
	@if pgrep -xq TaxBinder; then echo "TaxBinder is running: quit it first (⌘Q)"; exit 1; fi
	rm -rf /Applications/TaxBinder.app
	ditto "$(APP)" /Applications/TaxBinder.app
	@echo "installed /Applications/TaxBinder.app"

backend: samples  ## run the backend alone in a terminal (prints a login URL)
	uv run python -m taxbinder.sidecar --no-stdin-watchdog

clean:
	rm -rf $(DESKTOP)/build $(TAURI)/target $(TAURI)/binaries $(TAURI)/backend
