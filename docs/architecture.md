# TaxBinder: architecture

How TaxBinder is built: the records it keeps, where and how it stores them, how documents come
in and are recognised, how the checks work, how text is redacted, and how the app is kept safe.
What the app is for is in [product.md](product.md); how to work on the code is in
[CONTRIBUTING.md](../CONTRIBUTING.md).

The code cites sections by number ("§4.1 write discipline"); `tests/test_docs.py` checks that
every number it cites is a section here.

Every example is from the invented household used throughout the tests and the demo: Avery and
Jordan Quill, the Quill Family Trust, and the institutions they deal with (Fernhollow
Properties, Copperfield Collective, Brackley Wealth, Tamarack Savings Bank, Linden Education).
None of it is real.

## 1. Overview

TaxBinder is a macOS app for a household that hands its tax documents to a preparer. It keeps
the documents in one encrypted folder on the Mac (the **vault**, called "your binder" in the
app), knows which documents to expect each year, notices what's missing or inconsistent, and
makes the files for the preparer. It doesn't prepare returns or give tax advice, and it calls
no AI service on the internet. Forms are read by deterministic rules and, once the person has
downloaded it, by a model running on the Mac (§5.6, §5.7); the two are compared, and every other
decision is a deterministic rule. The code avoids Apple-only technology, so that a Windows
version can follow; what has to differ per system (the key store, signing, the installer) is
kept in one place each.

Three parts:

- **The shell** (`desktop/`, Rust, Tauri 2): the window, the menus, the portal windows for
  institution websites. It starts the backend and talks to it over a pipe.
- **The backend** (`src/taxbinder/`, Python): a FastAPI server on 127.0.0.1 serving the pages
  (Jinja templates, with their own stylesheets and scripts), and the same code as a command line
  (`taxbinder …`). It's frozen into one program file with PyInstaller.
- **The vault**: a folder of YAML records under git, plus encrypted files (§4).

```
 TaxBinder.app
 ├─ shell (Rust) ── main window ──HTTP──▶ backend (Python, 127.0.0.1:<port>)
 │                └ portal windows (institution sites; downloads → inbox)
 └─ backend ── RecordStore ──▶ vault/records (YAML, git)  +  vault/blobs (*.age)
```

## 2. What it catches

The checks exist for situations like these, all from the invented household's 2025 (they're the
acceptance tests in `tests/test_reconcile.py`, §16).

### 2.1 Amounts that disagree

The household's own checklist says the Quills paid a federal estimated total of $32,000; the
payments entered add up to $36,000. A finding says so, as a possible inconsistency, and suggests
confirming the total with the preparer. The same kind of check compares a donor-advised fund
gift entered in two places.

### 2.2 Dates that can't be right, and documents not filed

Copperfield Collective's 2025 K-1s for Ashcombe Partners and Corvina Ledger Fund are marked
received in September 2025, before the tax year they report on had ended: a date outside the
plausible window, flagged as a possible inconsistency. A document marked received but not yet
stored in the vault is flagged too, and so is a document still to come as its deadline nears.

### 2.3 Claims without their evidence

A $2,000 cash gift to the Friends of the Marsh Wrens needs the charity's written
acknowledgment; a gift through a donor-advised fund needs the sponsor's acknowledgment and, for
a non-cash gift, the records for Form 8283. Until a document of the right kind is linked, the
claim stays open (§8.3).

### 2.4 Timing

The first federal estimated payment was made on April 18, after the first installment's due
date: the first installment is flagged as missing for its period.

## 3. Concepts

### 3.1 Who and where

**Taxpayers** are the people and trusts in the household (Avery, Jordan, the Quill Family
Trust). **Institutions** send documents (Fernhollow Properties, Brackley Wealth). A **source**
is one thing at an institution that produces documents each year: an account, a fund holding,
an employer relationship ("Fernhollow Properties · Kettle 410", "Brackley Wealth · Interest").
Sources belong to a taxpayer (§5.1, §5.2).

### 3.2 Document types

Built-in types are the forms the app knows (W-2, 1099-INT, 1099-DIV, consolidated 1099, K-1s,
1098, 5498, payment confirmations, donation letters, returns…). The person can add their own
(`x-…`, §5): a name, identifying words, a folder for the preparer.

### 3.3 Expectations and applicability

An **expectation** is a checklist row: "this source should produce this document type for this
tax year". Each row has an **applicability** (applicable, not applicable this year, not sure),
and marking a row not applicable needs a short reason, so it can be remembered next year. A new
year starts from the previous one's sources and rows, with statuses fresh (§8.4); an earlier
year from the first one's, where a row left out ends nothing.

### 3.4 Claims

A **claim** is a fact with an amount that isn't a document: a tax payment, a donation, a
property tax or vehicle bill, a foreign account for FBAR. A claim can carry **assertions**
(what different places say about it) and a **resolution** when they disagree, and it collects
**requirements**: evidence rules ask for (§8.3).

### 3.5 Documents

A **document** is a stored file, never changed once stored (its id comes from its content). A
corrected 1099 or K-1 is a new document that **supersedes** the old one; both are kept, and the
newer is current. Exact copies are skipped when they arrive.

## 4. Storage: the vault

One vault per filer: the household's, and one for each trust or business that files its own
return (§11.1), in `entities/<slug>/vault` beside it. The household's vault lives in
`~/Library/Application Support/com.4ampersand.taxbinder/vault`; development uses a throwaway one
(`TAXBINDER_VAULT`).

### 4.1 Write discipline

All reading and writing goes through `RecordStore` (`store/records.py`):

- one store per vault at a time (a file lock);
- every write happens inside `store.operation(actor, message)`, and each operation is **one git
  commit** in `records/` (dulwich, no git binary): the audit trail, and what Undo goes back
  through. Operations nest: one started inside another joins it, so a user's action is one
  change and one Undo;
- files are written atomically; each record has a `rev` (the SHA-256 of its file), and a write
  states the rev it expects, so two writers can't silently overwrite each other;
- in the app, every write goes through `act()` in `web/app.py`, which turns the result into a
  reply that names the change (for the toast's Undo).

A change interrupted half-way is put back when the vault next opens (the records git knows
about are restored from the last commit).

### 4.2 Layout

```
vault/
  records/                 git repository (YAML)
    vault.yaml             id, format, recipients (public keys), key check
    registry/              taxpayers/, institutions/, sources/, doc-types/,
                           settings.yaml, entity.yaml, matrix.yaml, privacy.yaml, …
    years/2025/            year.yaml, expectations/, claims/<category>/, findings.yaml,
                           figures.yaml
    documents/             one YAML per document (metadata only)
  blobs/ab/12/<sha256>.pdf.age    the files themselves, encrypted (§4.5)
  inbox/                   files waiting to be read (removed once stored)
  index.sqlite             derived, rebuildable (§4.7)
  private/                 this Mac's sharing certificate and paired devices
```

`vault.yaml`'s `format` says which version of the layout the vault uses; a newer TaxBinder
upgrades older formats (after making a backup, `store/upgrade.py`), and an older TaxBinder
refuses a newer vault before writing anything.

### 4.3 Records and their ids

Each record type has a fixed place (`store/kinds.py`), and its id is its path:
`2025/copperfield-collective.ashcombe-partners.k1` is
`years/2025/expectations/copperfield-collective.ashcombe-partners.k1.yaml`. Ids are validated
before they become paths. Record shapes are Pydantic models (§5); `schemas/` holds their JSON
Schema, regenerated by `taxbinder schema export` and checked by a test.

### 4.4 Evidence links

A link from evidence to what it proves is stored on the side that needs the evidence: an
expectation or a claim lists the documents that satisfy it (`evidence: [{doc, role, linked_at,
linked_by}]`). A superseded document's link stays, marked `superseded`, so history shows what
was there. Links are never moved by the app on its own; matching again only fills empty ones.

### 4.5 Files: content-addressed and encrypted

A stored file is kept once, under its SHA-256 (`blobs/ab/12/ab12…pdf.age`), encrypted with
[age](https://age-encryption.org) (pyrage) to the vault's recipients: this Mac's vault key and
the recovery key (§14). Reading a file checks it against its hash. Text read from scans (§6.2)
and the redaction map (§9.3) are encrypted the same way.

### 4.6 Encrypted fields

Records are plain YAML, but identifier fields are encrypted inside them: `number`,
`account_number`, `tin`, `issuer_tin`, `ssn`, `ein`, `routing_number`, `confirmation_number`
and `secret` are written as `ENC[age:…]` (`store/crypto.py`, `ENCRYPTED_FIELDS`). Pages and
findings show identifiers only as hints (`…7731`). Names, amounts and dates are not encrypted
in the records; the privacy statement on the Help page says so, and backups are encrypted whole
(§14).

### 4.7 The index

`index.sqlite` is a derived index for fast lists and search, rebuilt from the records at any
time (`taxbinder reindex`); it's never backed up.

## 5. The domain model

`src/taxbinder/models/` is the single source of truth for record shapes. The examples below are
the invented fixtures in `tests/fixtures/examples/`, shortened.

### 5.1 Institution

```yaml
id: fernhollow-properties
name: Fernhollow Properties
aliases: [Fernhollow Properties LLC]
tin: ENC[age:…]          # its EIN as printed on its forms
```

A read name finds its institution however it's spelled (the EIN, then the first word:
`institutions.py`), or becomes a new one when the document is stored. An issuer is one EIN
each document keeps the EIN it was read with (`Classification.issuer_tin`, encrypted);
two different EINs never join, whatever the names; the model's EIN is used only when the rules
read none and it's printed on the page or already known; the same EIN under a wholly new name
stays apart and is offered as a note in Noticed for you (`same_ein`).

### 5.2 Source

```yaml
id: fernhollow-properties.kettle-410
institution: fernhollow-properties
name: Kettle 410
owner: avery
identifiers:
  - {number: ENC[age:…], hint: "…", kind: primary}
produces:
  - {doc_type: k1_1065, federal: true}
  - {doc_type: k1_state, states: [CO], optional: true}
expected_each_year: true
```

#### 5.2a Matching keys

A source is found for a document by its institution, its identifiers (primary, legacy,
alternate, reference: accounts renumbered keep their history) and the keys of its name ("Kettle
410", "HOP" for "Hawksmoor Office Park"). Matching itself is §6.2.

### 5.3 Expectation

```yaml
id: 2025/copperfield-collective.ashcombe-partners.k1
year: 2025
source: copperfield-collective.ashcombe-partners
doc_type: k1_1065
applicability: applicable
acquisition: expected     # expected → received → vaulted (or superseded)
review: unreviewed        # checked against the paper, if the person ticks documents
delivery: not_sent        # sent to the preparer: set by the person, never by the app
expected_available_at: 2026-09-30
evidence: []
```

The three statuses are separate on purpose: receiving a document doesn't mark it checked, and
checking it doesn't mark it sent.

### 5.4 Document

```yaml
id: doc_ab12cd34ef56
sha256: ab12cd34…
blob: ab/12/ab12cd34….pdf.age
original_filename: Kettle410_2025_K1_Final.pdf
classification:
  doc_type: k1_1065
  tax_year: 2025
  issuer: fernhollow-properties
  source: fernhollow-properties.kettle-410
  confidence: 0.97
  method: rules            # filename, rules, human: how each part was decided
version: {supersedes: null, is_corrected: false}
recipient: {printed: Avery Quill, taxpayers: [avery]}
tax_return: null           # a return: its form, status and what it lists
tags: []
```

`method` gains `human` when the person corrects the type, year or sender; tags record other
corrections (`whose-changed`, `moved-here`). The diagnostic info counts them.

### 5.5 Claims and bills

```yaml
id: 2025/donations/2025-02-26.friends-of-the-marsh-wrens
category: donation
date: 2025-02-26
payee: Friends of the Marsh Wrens
amount: 2000.00
donation: {type: cash, method: credit_card, quid_pro_quo: false}
requirements:
  - {id: written_acknowledgment, rule: R-DON-01, satisfied_by: null}
evidence: []
```

Claims are one union, discriminated by `category`: `tax_payment`, `estimate`, `donation`,
`property_tax`, `vehicle`, `fbar`. A bill not yet paid is a claim with a **plan** (due date,
scheduled date, payee); a scheduled payment doesn't count as paid until it is.

### 5.6 Extractors

`extract/` reads documents by the place of things on the form (with the local model of §5.7;
nothing goes to an AI service): `parties.py` reads the issuer, recipient and account (the
line under the caption, else a letterhead); `forms.py` finds each known form's main page by its
title and OMB number and reads all its boxes, with the form's own arithmetic checks;
`crosscheck.py` compares them with the model's reading, box by box; `figures.py` gives Across
the years the boxes it asks for,
from `forms.py` first and by caption for the other forms, offered as suggestions (one that fails
a check is marked "doesn't add up" and waits to be looked at; where the model and the rules
differ, the rules' value shows "to check" with both, kept with ✓ or left empty with ✕);
`return1040.py` reads what a 1040 says about the household and the payers it lists,
kept as suggestions on the document until the person confirms them.

### 5.7 The reading engine

`engine.py` runs llama.cpp's `llama-server` (MIT, release pinned with its checksum in
`desktop/scripts/fetch_engine.py`, bundled beside the backend and signed with it) for the model
that reads forms with the rules. The model is downloaded by the app after asking (its size
shown), checked against the checksum in `reading_models.json`, and kept beside the vault, not in
it or its backups; a newer list of models, signed by the publisher, can replace that file
(`llm.py`). It's started only while documents are being read and
stopped after five idle minutes or when the app quits; a run that ended abruptly leaves a pid
file, and the next start stops that engine first. It listens on 127.0.0.1 on a new port each
time, answers only with the key made for that start (in its environment, not on its command
line), works offline, without its web page or tools, and its output is discarded.

## 6. Ingestion

### 6.1 The inbox

Files arrive in `vault/inbox/` (dropped on Add documents, saved from a portal window, or put in
the folder by hand). Each is hashed: an exact copy of a stored file is skipped. Then it's read,
classified, matched and stored in **one operation** (`ingest/inbox.py`), and removed from the
inbox. Files from the inbox and portal windows wait for **Check** (`waiting`) before they're
filed, so nothing is filed unseen.

### 6.2 Read, classify, match

- **Text**: the PDF's text layer (pypdfium2 and pdfplumber, `pdfdoc.py`), held in memory only.
  A scan or photo is read by text recognition inside the app (`ocr.py`: PaddleOCR's PP-OCRv6
  models run by ONNX Runtime, the same on Mac and Windows; a tilted page is straightened
  first), cached encrypted with the document. The models are bundled by the build; the app
  never downloads them.
- **Classify** (`ingest/classify.py`): the form named earliest in the text wins (a document's
  own title is at the top; a consolidated 1099 that mentions "Form 1099-DIV" inside stays
  consolidated); the tax year from the form's own wording; the issuer and account read by
  their place (§5.6). A tax return is recognised by its printed title, never a form number
  (`returns.py`), and is no checklist row's document.
- **Whose** (`whose.py`): the recipient's name → a person, both, or a trust; unclear → asked on
  Check, never guessed.
- **Match** (`ingest/match.py`): candidates are the year's rows of the same type (or a
  sibling: a dividend payer's consolidated-1099 row also takes a 1099-DIV), scored by the
  issuer, the account number and the source's name. A document is linked by itself only when
  the best score is clear of every rival; otherwise Check asks.
- **Check** (`check.py`): groups what was found by institution, asks only what it can't be sure
  of (type, year, sender, whose, a copy, which copy of a return, a scan to look at), and files
  everything in one change.

## 7. Collecting documents

### 7.1 Portal windows

The person opens an institution's website in a separate window of the app (or their browser,
by choice). What they download there goes into the inbox and waits for Check. "Save Page to
TaxBinder" saves the document the window shows. Portal windows get no access to the backend.
There are no automatic downloads (email, scripted sign-ins): the person fetches each document.

### 7.2 The last attempt

An expectation can record the last attempt to find its document (`last_check`): `not_found`
(looked, not there yet) is different from `could_not_check` (couldn't look).

## 8. Rules

### 8.1 The framework

A rule is a function over the year's records (`rules/engine.py`), registered with `@rule(...)`
and an id (`R-PAY-01`). It yields either:

- `Flag(subjects, message, …)`: a **finding**, with a severity (error, warning, info) and a
  classification (missing evidence, change, possible inconsistency, demonstrated error); or
- `Require(claim, requirement)`: evidence a claim needs.

`reconcile(store, year)` runs every rule and saves the findings (`years/<y>/findings.yaml`).
A finding's id is stable (rule, year, subject), so it keeps its history: first seen, last seen,
acknowledged by the person with a note (it stays quiet until its message changes), resolved
when the rule stops flagging it. Findings never print a full account number.

```yaml
id: R-PAY-01:2025:federal
rule: R-PAY-01
severity: error
classification: possible_inconsistency
subjects: ["2025", 2025/payments/fed.2025-04-18, 2025/payments/fed.2025-06-13,
           2025/payments/fed.2025-09-12, 2025/payments/fed.2026-01-14]
message: "federal payments total $36,000.00 (4 payments) but fixture:summary says $32,000.00"
suggested_action: Find the missing or extra payment, or correct the summary
status: open
```

#### 8.1a Parameters

Due dates, thresholds and rates are per tax year and per return form,
`rules/params/<year>[-<form>].yaml` (`2025.yaml` for a 1040, `2025-1041.yaml` for a trust). A
file marked `reviewed_by` has been checked by a person; its values aren't changed without saying
so. Each finding records the hash of the file it was made with (`params_hash`), so a changed
parameter shows. A year without a file isn't checked, and the app says so.

### 8.2 The catalog

One module per area in `rules/catalog/`: `expectations` (late, received but not filed,
superseded after sending), `payments` (totals, installments, timing), `donations` (letters,
Form 8283 records), `consistency` (the same fact in two places), `sources`, `foreign` (FBAR).

### 8.3 Requirements and evidence

A requirement is satisfied by a linked document of an accepted type and the right tax year, or
by a **waiver** (a reason, noted). An unsatisfied one becomes a `missing_evidence` finding. A
donation stays open until its evidence is linked.

### 8.4 Applicability

Whether a document should exist this year is the person's decision, with a reason
(`applicability`, §3.3), never a rule's.

## 9. Redaction

To ask an AI chat about documents without giving it personal details, TaxBinder makes a
redacted copy of their text, at the Mac only. The app itself never sends it anywhere.

### 9.1 Redacted Markdown

`redact/markdown.py` turns documents into Markdown with page markers, every personal detail
replaced by a placeholder. A **leak check** runs last: if any value the vault knows is still in
the text, nothing is produced (no partial text is ever copied).

### 9.2 What to hide

`redact/detect.py`: values the vault knows first (every TIN, EIN, account number, name and
address, in the forms they're printed in: with or without dashes, masked to the last 4,
middle initials, street abbreviations), then patterns for the rest (SSNs, EINs, account numbers
next to "account", street addresses, ZIP codes, email, phone). The person can add values to
hide (Privacy for AI chats, `privacy.py`); they're stored encrypted.

### 9.3 Placeholders

`redact/tokens.py`: the same real value always gets the same placeholder, in every document
and year (`[TIN_01]`, `[PERSON_02]`), so a chat's answer can be turned back (`taxbinder unredact`).
The map lives in the vault, encrypted, and is never exported.

## 10. Analysis

Everything that judges is deterministic: rules (§8), the comparisons of Across the years (big
changes from the year before: at least 25% and $500, or a change of sign, stated as facts),
the year's three answers (still to come, needs attention, ready for the preparer: `results.py`)
and Noticed for you (`notices.py`). The local model only reads forms, and what it reads is
compared with the rules before it's used. The app calls no AI service on the internet.

## 11. The app

### 11.1 Filers and sharing

Each filer (the household, a trust, a business) has its own vault (§4), opened when first
shown; documents never move to another filer's vault by themselves. **Home sharing** opens the
binder to paired devices on the home network: a second, HTTPS listener with its own
certificate, off until turned on; pairing takes a short code shown on the Mac (§12.2).

### 11.2 Your binder: the deck of years

Home (`/`) is the binder as a deck of year cards (`binder.py`, `web/binder_views.py`):
one card per year, the year being worked on in front, flat; the cards behind lean back from
their bottom edge and dim. On each card, kinds of document across and issuers down, each square
one issuer's documents of one kind in that year, built from Across the years' rows
(`matrix.rows(every=True)`). Each card's place is a CSS variable set by `app.js` (the policy
blocks inline `style` attributes, not styles set from a script); the deck itself takes no
clicks, since in 3D the cards set back sit behind their container.

Beside the card in front: the year's answers (`results.py`), its hand-off to the preparer, the
papers for the preparer, and "Noticed for you": rules over the binder's own years
(`notices.py`), each answered with one undoable click; a note set aside is kept in
`AppSettings.dismissed_notices`. Across the years (`/years`, `matrix.py`) is the same data as a
table, one row per source and kind of document, a column per year. Add documents (`/add`) takes
each file through its steps (`add.py`), shown as it happens, then asks what's unclear and what
doesn't come in the mail. The top bar: Your binder, Check with its badge (`journey.py`),
Documents, Across the years, the filer switcher, Last backup, Settings and Help.

### 11.3 Pages

Server-rendered Jinja templates in `web/templates/`, with their styles and scripts in
`web/static/`. Every page extends `base.html` (the top bar, the viewer, `app.css`, `bar.js`,
`app.js`); the binder's other pages (Documents, Payments, Donations, Foreign accounts, Changes,
Check, Settings, …) extend `page.html`, which adds a heading, the page's help, the binder's tabs,
`pages.css`, `grid.js` (sortable tables) and `pages.js`. On those pages (`body.pages`) the home
components in `app.css` stand back: they're scoped with `:where(body:not(.pages))`, which adds
no specificity.
The content security policy allows no inline script or style. One document viewer serves every
page: anything marked `data-view="<document id>"` (and `data-page`) opens it. Help on each page
(`help.py`), the privacy statement and the help topics are on `/help`.

### 11.4 Shell and backend

The shell starts the backend (`taxbinder.sidecar`) with a one-time code, reads control messages
from its output (open a portal window, print, the port), and closes it when the window closes.
The main window gets no access to Tauri's own interface: the pages are treated as remote
content. Menus (Save Page, Make Text Bigger) are handled by the shell itself.

### 11.5 The command line

`taxbinder …` (Typer, `cli.py`) runs the same code on `$TAXBINDER_VAULT`: status, reconcile,
ingest, years and rows, claims, redaction, backups, the preparer's folder. Its messages may name
commands; the app's never do (a test checks it). Every command is in [cli.md](cli.md).

## 12. Security

### 12.1 What it protects against

The data is a household's tax documents: identifiers that enable fraud, and income. TaxBinder
assumes the Mac and its user account are the person's own. It protects against: the files
being read off the disk or a backup without a key (encryption, §4.5, §14); other programs and
web pages on the same Mac talking to the backend (§12.2); devices on the network (sharing is
off by default and needs pairing); identifiers leaking into logs, findings, file names, the
diagnostic info or redacted text (masking, §9). It does not protect against someone using the
Mac as the person.

### 12.2 The local server

- Listens on 127.0.0.1, on a port the OS chooses; the Host header must be exactly
  `127.0.0.1:<port>` (against DNS rebinding).
- Every page except a few needs the session cookie, obtained once through `/bootstrap?t=<one-time
  code>`; the code only travels over the shell's pipe. The cookie is HttpOnly, SameSite=Strict.
- Every state-changing request must carry a matching Origin.
- No inline scripts; downloaded files are served as attachments with a sandboxing policy, never
  shown inside the app. The viewer (`viewer.py`) shows pictures of a document's pages
  instead, drawn on this Mac by pypdfium2 (a photo turned upright and re-encoded), sent as PNGs
  with `Cache-Control: no-store`; a saved web page isn't drawn at all.
- Logs never contain identifiers, cookie values or URL query strings (they can hold one-time
  tokens).

On the sharing listener, Host and Origin must be this Mac's `<name>.local:<port>` or its address
on the network, and every page except pairing needs a paired device's cookie (only a hash of it
is stored). Paired devices can't use what drives this Mac or holds its secrets: portal windows,
printing, saving into Downloads, backups, the recovery key, sharing itself, redaction
(`LOCAL_ONLY` in `web/app.py`).

## 13. The files for the preparer

### 13.1 The package

`package/build.py`: one filer, one year. The year's documents in numbered folders by type
(`01-W2`, `02-1099`, `03-K1`, …), named after institution and source with account-like digits
removed; `00-summary.pdf` (who's filing, what's here, what's still to come, the most important
open items, corrected documents, documents not expected this year); `index.csv`,
`open-items.csv`, the estimated payments' schedule, donations, an FBAR summary (`foreign.py`:
bank, account, currency, highest balance, rate, dollars), and `10-For-preparer` when the
year has papers for the preparer (`preparer.py`: documents tagged `for-preparer`, kept
untyped and never read, named by `Document.preparer_note`, the note also in the index). In the app it's
one .zip (`package/handoff.py`) saved into Downloads; TaxBinder remembers when it was made and
which documents went in, and marks it out of date when more arrive. It's not encrypted, and the
app never sends it or marks anything sent.

## 14. Backups and keys

Each vault has its own key, kept in the macOS Keychain (service `com.4ampersand.taxbinder`). Everything is
encrypted to two recipients: that key and the **recovery key**, shown once on Keep it safe and
never stored by TaxBinder. A **backup** (`backup.py`) is one `.tbak` file: every filer's vault
as an archive, encrypted whole to the same recipients, so names and amounts are unreadable
without a key. A restore goes into a new folder, never over a vault in use. The help topics
say what each brings back (a backup with a key: everything; a key alone: a vault still on the
Mac; a backup alone: nothing).

## 15. Hand-entered totals

A year can hold **summaries**: totals the person typed from elsewhere (the federal estimates
total on a checklist they keep, `YearConfig.summaries`), which rules compare with the detail
(§2.1). Across the years (`matrix.py`) keeps typed amounts beside amounts read from documents; a
read value never replaces a typed one.

## 16. Testing

`make test` runs ruff, pytest and the shell's Rust tests. Tests use the invented household
(`tests/quills.py`), PDFs generated in the tests (PyMuPDF, test-only) and the sample set
(`tests/samples/`, also the demo's documents); keys stay in memory (`TAXBINDER_KEYSTORE=memory`).
`tests/test_reconcile.py` reconciles the test household and expects every finding of §2;
`tests/test_package.py` builds its package for the preparer. Vaults made by earlier versions
must keep opening (`tests/test_old_vaults.py`, fixtures never edited). The model is never used
in tests; a stand-in plays the engine. `make privacy` checks that nothing from the
maintainer's own vault is in the repository.
