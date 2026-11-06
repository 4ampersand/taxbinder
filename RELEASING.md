# Releasing TaxBinder

For the maintainer. A release is a signed, notarized `TaxBinder.app` and
`TaxBinder.dmg`, published on the repository's GitHub releases.

## Once, on the release Mac

- A **Developer ID Application** certificate in the login Keychain (Xcode › Settings › Accounts ›
  Manage Certificates). With several, set `TAXBINDER_SIGN_IDENTITY` to the one to use.
- Notarization credentials saved as the Keychain profile `taxbinder-notary`:
  `xcrun notarytool store-credentials taxbinder-notary …`.
- For signing the list of reading models (below): `uv run python -m taxbinder.dev.model_list
  keygen`. It keeps the private key in this Mac's Keychain and prints the public key, which goes
  into `PUBLIC_KEY` in `src/taxbinder/llm.py`. The private key never goes in the repository;
  losing it means a new key and a new app release before newer lists can be published.

## Each release

1. **Check the repository.** `make privacy` (quit TaxBinder first): nothing from your own vault
   in any file, any version of a file, or any commit message. Do this before every push to the
   public repository, not only for releases.
2. **Raise the version** in `pyproject.toml`, `desktop/package.json`,
   `desktop/src-tauri/tauri.conf.json` and `desktop/src-tauri/Cargo.toml` (the same number in
   all four), and add one to the build number, `bundle.macOS.bundleVersion` in
   `tauri.conf.json` (About TaxBinder shows it in brackets: "Version 0.11.0 (2)").
3. **The vault format.** If a record's shape or the vault layout changed since the last
   release, the fixture, `FORMAT` and the upgrade step must already be in (see
   [CONTRIBUTING.md](CONTRIBUTING.md#changing-a-records-shape)).
4. **Pinned downloads.** To move the reading engine to a newer llama.cpp release, change
   `RELEASE` and the checksums in `desktop/scripts/fetch_engine.py`. The OCR models come from
   the `rapidocr` package, at the version in `uv.lock`. The reading models' list, each file
   pinned to one revision with its SHA-256, is `src/taxbinder/reading_models.json`.
5. **`make test`.**
6. **`make release`.** Builds the frozen backend (with the demo's sample files, the engine and
   the third-party licences file), the app and the `.dmg`; signs every program inside with the
   hardened runtime; notarizes and staples both; and leaves them in `build/release/`. It stops
   before building if the certificate or the notarization profile is missing.
7. **Try it** on a Mac account that has never run TaxBinder: it opens with no warning, Get
   started and the demo work, and an existing vault from the previous version opens.
8. **Publish** a GitHub release, tagged `v<version>`, with the `.dmg` and the reading models'
   list (a full release, not a pre-release: GitHub's "latest" skips pre-releases):
   ```bash
   cp src/taxbinder/reading_models.json build/release/
   uv run python -m taxbinder.dev.model_list sign build/release/reading_models.json
   gh release create v<version> build/release/TaxBinder.dmg build/release/reading_models.json \
     build/release/reading_models.json.sig --title "TaxBinder <version>" --notes "…"
   ```
   The download page's button links to the latest release's `TaxBinder.dmg`, so it needs no
   change. The app reads the new version from the latest release's tag (`updates.py`), so
   copies already installed say a newer one is out within a day.

## The download page

`site/` is the page at https://4ampersand.github.io/taxbinder: static, published by
`.github/workflows/pages.yml` whenever `site/` changes on main (Settings › Pages › Source: GitHub
Actions). Its screenshot is the invented household's.

## A newer list of reading models

When a better model is available, add it to a copy of `reading_models.json` (each file pinned to
one revision, with its SHA-256 and size), then sign it:

```bash
uv run python -m taxbinder.dev.model_list sign reading_models.json   # writes reading_models.json.sig
```

Attach both files to the latest GitHub release. The app takes a newer list only with a valid
signature, and keeps the model in use until a new one has been downloaded and checked.

## Licences

`make licences` (run by `make sidecar`) writes the third-party licences file the app bundles: the
Python packages, the Rust crates and llama.cpp, with their licence texts. A package without a
licence text stops the build. Help › About shows the file.
