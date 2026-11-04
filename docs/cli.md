# `taxbinder`

TaxBinder: track and reconcile tax documents.

**Usage**:

```console
$ taxbinder [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--entity <str>`: Which entity&#x27;s vault to work on (see `taxbinder entity list`)  [env var: TAXBINDER_ENTITY; default: household]
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `init`: Create an empty, encrypted vault; prints...
* `restore-key`: Put the vault key back into this Mac&#x27;s...
* `reindex`: Rebuild index.sqlite from the record files.
* `fsck`: Check records, blob references and blob...
* `ingest`: Add documents: the given files (copied,...
* `reconcile`: Run the rules for a year and store the...
* `status`: What is missing, what conflicts, what...
* `ack`: Acknowledge a finding; it stays quiet...
* `link`: Link a document as evidence for an...
* `waive`: Waive an evidence requirement on a claim,...
* `verify`: Mark documents as checked (review =...
* `unverify`: Undo `verify` (review = unreviewed).
* `rematch`: Match again every document linked to...
* `redact`: Documents as one redacted Markdown text,...
* `unredact`: Put the real values back into an LLM&#x27;s...
* `history`: Recent changes to the vault, newest first...
* `undo`: Put back the records a change touched, as...
* `schema`: JSON Schema for record types.
* `dev`: Developer tools.
* `claim`: Record claims (donations, payments).
* `applicability`: Applicability decisions.
* `year`: Tax years.
* `row`: Checklist rows (expected documents).
* `package`: The files for your preparer, for one year.
* `key`: This Mac&#x27;s vault key.
* `backup`: Encrypted backups of every vault.
* `acrossyears`: Across the years: each document&#x27;s key...
* `privacy`: Private details to hide when redacting...
* `doctype`: Document types you add.
* `institution`: Institutions: who sends documents.
* `return`: Tax returns (draft or filed).
* `settings`: The app&#x27;s settings.
* `household`: The household.
* `document`: Whose a document is; moving it.
* `entity`: Entities that file their own return.
* `bill`: Bills and scheduled payments.

## `taxbinder init`

Create an empty, encrypted vault; prints the recovery key once.

**Usage**:

```console
$ taxbinder init [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder restore-key`

Put the vault key back into this Mac&#x27;s Keychain using the printed recovery key.

**Usage**:

```console
$ taxbinder restore-key [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder reindex`

Rebuild index.sqlite from the record files.

**Usage**:

```console
$ taxbinder reindex [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder fsck`

Check records, blob references and blob integrity. Exit code 1 on problems.

**Usage**:

```console
$ taxbinder fsck [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder ingest`

Add documents: the given files (copied, never moved), or everything in the inbox.

**Usage**:

```console
$ taxbinder ingest [OPTIONS] [paths]...
```

**Arguments**:

* `paths...`: Files to add

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder reconcile`

Run the rules for a year and store the findings.

**Usage**:

```console
$ taxbinder reconcile [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder status`

What is missing, what conflicts, what needs attention.

**Usage**:

```console
$ taxbinder status [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--outstanding / --no-outstanding`: List expectations not in the vault  [default: no-outstanding]
* `--all`: Show every finding
* `--help`: Show this message and exit.

## `taxbinder ack`

Acknowledge a finding; it stays quiet until its message changes.

**Usage**:

```console
$ taxbinder ack [OPTIONS] {finding}
```

**Arguments**:

* `finding`: Finding id, as shown by `status`  [required]

**Options**:

* `--note <str>`: Why this is fine  [required]
* `--year <int>`: Tax year  [default: (last year)]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder link`

Link a document as evidence for an expectation or a claim.

**Usage**:

```console
$ taxbinder link [OPTIONS] {doc} {target}
```

**Arguments**:

* `doc`: Document id (doc_…)  [required]
* `target`: Expectation or claim id  [required]

**Options**:

* `--role <str>`: primary or supporting  [default: primary]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder waive`

Waive an evidence requirement on a claim, with a reason and note.

**Usage**:

```console
$ taxbinder waive [OPTIONS] {claim} {requirement}
```

**Arguments**:

* `claim`: Claim id  [required]
* `requirement`: Requirement id, e.g. written_acknowledgment  [required]

**Options**:

* `--reason <str>`: e.g. not_required, lost, other  [required]
* `--note <str>`: Explanation  [required]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder verify`

Mark documents as checked (review = verified); clears their R-EXP-01 finding.

**Usage**:

```console
$ taxbinder verify [OPTIONS] {expectations}...
```

**Arguments**:

* `expectations...`: Expectation ids, as shown by `status`  [required]

**Options**:

* `--note <str>`: What you checked, e.g. amounts match the statement  [required]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder unverify`

Undo `verify` (review = unreviewed).

**Usage**:

```console
$ taxbinder unverify [OPTIONS] {expectations}...
```

**Arguments**:

* `expectations...`: Expectation ids, as shown by `status`  [required]

**Options**:

* `--note <str>`: Why, e.g. verified by mistake  [required]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder rematch`

Match again every document linked to nothing; existing links are never moved.

**Usage**:

```console
$ taxbinder rematch [OPTIONS]
```

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder redact`

Documents as one redacted Markdown text, safe to paste into an LLM chat.

**Usage**:

```console
$ taxbinder redact [OPTIONS] {docs}...
```

**Arguments**:

* `docs...`: Document ids (see the Documents page)  [required]

**Options**:

* `--out <path>`: Write the Markdown here (default: print it)
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder unredact`

Put the real values back into an LLM&#x27;s answer: paste it on stdin.

**Usage**:

```console
$ taxbinder unredact [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder history`

Recent changes to the vault, newest first (ids for `taxbinder undo`).

**Usage**:

```console
$ taxbinder history [OPTIONS]
```

**Options**:

* `--all`: Include automatic re-checks
* `--limit <int>`: How many  [default: 30]
* `--help`: Show this message and exit.

## `taxbinder undo`

Put back the records a change touched, as they were before it (recorded as a change).

**Usage**:

```console
$ taxbinder undo [OPTIONS] {change}
```

**Arguments**:

* `change`: Change id from `taxbinder history` (12+ chars)  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder schema`

JSON Schema for record types.

**Usage**:

```console
$ taxbinder schema [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `export`: Write JSON Schema for every record type.

### `taxbinder schema export`

Write JSON Schema for every record type.

**Usage**:

```console
$ taxbinder schema export [OPTIONS]
```

**Options**:

* `--out <path>`: Output directory  [default: schemas]
* `--help`: Show this message and exit.

## `taxbinder dev`

Developer tools.

**Usage**:

```console
$ taxbinder dev [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `layout`: A document&#x27;s layout with every digit...

### `taxbinder dev layout`

A document&#x27;s layout with every digit replaced by 9, to share for tuning the readers.
Names and words are left as they are: look it over before sharing.

**Usage**:

```console
$ taxbinder dev layout [OPTIONS] {doc}
```

**Arguments**:

* `doc`: Document id (Documents page)  [required]

**Options**:

* `--help`: Show this message and exit.

## `taxbinder claim`

Record claims (donations, payments).

**Usage**:

```console
$ taxbinder claim [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `donation`: Record a donation (then `reconcile`...
* `payment`: Record an estimated, extension or...

### `taxbinder claim donation`

Record a donation (then `reconcile` attaches the evidence it needs).

**Usage**:

```console
$ taxbinder claim donation [OPTIONS]
```

**Options**:

* `--payee <str>`: Charity or DAF  [required]
* `--amount <str>`: Amount in USD  [required]
* `--date <str>`: YYYY-MM-DD  [required]
* `--type <cash|securities|property>`: [default: cash]
* `--to-daf`: Contribution to a DAF
* `--taxpayer <str>`: Taxpayer id
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder claim payment`

Record an estimated, extension or balance-due payment.

**Usage**:

```console
$ taxbinder claim payment [OPTIONS]
```

**Options**:

* `--jurisdiction <str>`: federal, CA, IL, …  [required]
* `--amount <str>`: Amount in USD  [required]
* `--date <str>`: YYYY-MM-DD  [required]
* `--kind <estimated_q1|estimated_q2|estimated_q3|estimated_q4|extension|balance_due>`: What the payment is for  [required]
* `--year <int>`: Tax year it applies to
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder applicability`

Applicability decisions.

**Usage**:

```console
$ taxbinder applicability [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `set`: Decide whether a document should exist...

### `taxbinder applicability set`

Decide whether a document should exist this year.

**Usage**:

```console
$ taxbinder applicability set [OPTIONS] {expectation} {value}:<applicable|not_applicable|not_issued|deferred_next_year|below_threshold|final_year_prior|consolidated_elsewhere|other>
```

**Arguments**:

* `expectation`: Expectation id  [required]
* `value:<applicable|not_applicable|not_issued|deferred_next_year|below_threshold|final_year_prior|consolidated_elsewhere|other>`: [required]

**Options**:

* `--note <str>`: Required unless applicable
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder year`

Tax years.

**Usage**:

```console
$ taxbinder year [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `new`: Start a year from another&#x27;s checklist rows...

### `taxbinder year new`

Start a year from another&#x27;s checklist rows (statuses reset), adding only what&#x27;s missing.

**Usage**:

```console
$ taxbinder year new [OPTIONS] {year}
```

**Arguments**:

* `year`: The year to start, e.g. 2026  [required]

**Options**:

* `--from <int>`: Copy this year&#x27;s rows; default: the year before
* `--drop <str>`: A row of --from that won&#x27;t recur (repeatable); default: the suggested
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder row`

Checklist rows (expected documents).

**Usage**:

```console
$ taxbinder row [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a checklist row.
* `edit`: Change a row&#x27;s amount or comment.
* `delete`: Delete a checklist row (refused when files...

### `taxbinder row add`

Add a checklist row.

**Usage**:

```console
$ taxbinder row add [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [required]
* `--institution <str>`: Institution name, existing or new  [required]
* `--source <str>`: Account / fund / employer name, existing or new  [required]
* `--doc-type <str>`: e.g. k1_1065, 1099_composite, w2, or one added (x-…)  [required]
* `--owner <str>`: Taxpayer id, e.g. primary; default: the taxpayer, or household
* `--amount <str>`: Amount, e.g. 1234.50
* `--comment <str>`: A note for the row
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder row edit`

Change a row&#x27;s amount or comment.

**Usage**:

```console
$ taxbinder row edit [OPTIONS] {expectation}
```

**Arguments**:

* `expectation`: Row (expectation) id  [required]

**Options**:

* `--amount <str>`: New amount; empty to clear
* `--comment <str>`: New comment; empty to clear
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder row delete`

Delete a checklist row (refused when files are attached).

**Usage**:

```console
$ taxbinder row delete [OPTIONS] {expectation}
```

**Arguments**:

* `expectation`: Row (expectation) id  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder package`

The files for your preparer, for one year.

**Usage**:

```console
$ taxbinder package [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `build`: Build the folder for the preparer:...

### `taxbinder package build`

Build the folder for the preparer: documents, index.csv, open-items.csv, cover memo.

**Usage**:

```console
$ taxbinder package build [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--out <path>`: Folder to create
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder key`

This Mac&#x27;s vault key.

**Usage**:

```console
$ taxbinder key [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `show`: Print this Mac&#x27;s vault key, to keep in a...

### `taxbinder key show`

Print this Mac&#x27;s vault key, to keep in a password manager or on paper. It opens your
vault, its entities and every backup, like the recovery key: use it if that one is lost.

**Usage**:

```console
$ taxbinder key show [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder backup`

Encrypted backups of every vault.

**Usage**:

```console
$ taxbinder backup [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `create`: One encrypted file with the household&#x27;s...
* `restore`: Unpack a backup into a new folder with the...
* `status`: When the last backup was made from this Mac.

### `taxbinder backup create`

One encrypted file with the household&#x27;s vault and every entity&#x27;s (opens only with this
Mac&#x27;s key or the recovery key). Safe to keep in a cloud drive.

**Usage**:

```console
$ taxbinder backup create [OPTIONS]
```

**Options**:

* `--out <path>`: Folder for the backup file  [default: ~/Downloads]
* `--help`: Show this message and exit.

### `taxbinder backup restore`

Unpack a backup into a new folder with the recovery key (never over a vault in use).

**Usage**:

```console
$ taxbinder backup restore [OPTIONS] {file}
```

**Arguments**:

* `file`: A .tbak file  [required]

**Options**:

* `--to <path>`: A new, empty folder to unpack into  [required]
* `--help`: Show this message and exit.

### `taxbinder backup status`

When the last backup was made from this Mac.

**Usage**:

```console
$ taxbinder backup status [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder acrossyears`

Across the years: each document&#x27;s key amounts, year by year.

**Usage**:

```console
$ taxbinder acrossyears [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `show`: Print Across the years (whole dollars).
* `read`: Read each row&#x27;s boxes from every year&#x27;s...
* `set`: Type one amount: a row (its source and...

### `taxbinder acrossyears show`

Print Across the years (whole dollars).

**Usage**:

```console
$ taxbinder acrossyears show [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder acrossyears read`

Read each row&#x27;s boxes from every year&#x27;s PDF into suggestions (accept them in the app).

**Usage**:

```console
$ taxbinder acrossyears read [OPTIONS]
```

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder acrossyears set`

Type one amount: a row (its source and kind of document) in one year.

**Usage**:

```console
$ taxbinder acrossyears set [OPTIONS] {source} {doc_type} {year} {value}
```

**Arguments**:

* `source`: Source id, e.g. fernhollow-properties.kettle-410  [required]
* `doc_type`: e.g. k1_1065  [required]
* `year`: [required]
* `value`: &#x27;4535&#x27; or, for two boxes, &#x27;4535 / 9764&#x27;  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder privacy`

Private details to hide when redacting (stored encrypted).

**Usage**:

```console
$ taxbinder privacy [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a value to hide.
* `list`: The private details, masked.
* `remove`

### `taxbinder privacy add`

Add a value to hide. It&#x27;s asked for (not an argument), so it stays out of shell history.

**Usage**:

```console
$ taxbinder privacy add [OPTIONS] {kind}
```

**Arguments**:

* `kind`: name, address, number, email, phone or other  [required]

**Options**:

* `--note <str>`: Who or what it is, e.g. &#x27;daughter&#x27;
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder privacy list`

The private details, masked.

**Usage**:

```console
$ taxbinder privacy list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder privacy remove`

**Usage**:

```console
$ taxbinder privacy remove [OPTIONS] {number}
```

**Arguments**:

* `number`: The number `privacy list` shows  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder doctype`

Document types you add.

**Usage**:

```console
$ taxbinder doctype [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a document type (then use its id with...
* `list`: Built-in and added document types.
* `merge`: Move every document of an added type to...

### `taxbinder doctype add`

Add a document type (then use its id with `row add --doc-type`).

**Usage**:

```console
$ taxbinder doctype add [OPTIONS] {name}
```

**Arguments**:

* `name`: e.g. &#x27;Form 592-B&#x27;  [required]

**Options**:

* `--folder <str>`: Accountant package folder, e.g. 03-K1  [default: 09-Other]
* `--keyword <str>`: Words that identify it (repeatable)
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder doctype list`

Built-in and added document types.

**Usage**:

```console
$ taxbinder doctype list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder doctype merge`

Move every document of an added type to another type (one undoable change).

**Usage**:

```console
$ taxbinder doctype merge [OPTIONS] {old} {new}
```

**Arguments**:

* `old`: An added type, e.g. x-tax-return  [required]
* `new`: The type its documents become, e.g. tax_return  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder institution`

Institutions: who sends documents.

**Usage**:

```console
$ taxbinder institution [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: Every institution, with what keeps it (or...
* `remove`: Remove an institution nothing uses, e.g.

### `taxbinder institution list`

Every institution, with what keeps it (or that nothing does).

**Usage**:

```console
$ taxbinder institution list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder institution remove`

Remove an institution nothing uses, e.g. a misread name left behind (undoable).

**Usage**:

```console
$ taxbinder institution remove [OPTIONS] {inst_id}
```

**Arguments**:

* `inst_id`: Its id, from `institution list`  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder return`

Tax returns (draft or filed).

**Usage**:

```console
$ taxbinder return [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: The year&#x27;s returns and return papers.
* `status`: Say whether a return is a draft or the...
* `show`: What a 1040 says: household, and what it...
* `add-listed`: Checklist rows for what the return names...

### `taxbinder return list`

The year&#x27;s returns and return papers.

**Usage**:

```console
$ taxbinder return list [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--help`: Show this message and exit.

### `taxbinder return status`

Say whether a return is a draft or the filed copy.

**Usage**:

```console
$ taxbinder return status [OPTIONS] {doc} {status}
```

**Arguments**:

* `doc`: Document id, e.g. doc_0123456789ab  [required]
* `status`: draft | filed | unsure  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder return show`

What a 1040 says: household, and what it lists against the checklist.

**Usage**:

```console
$ taxbinder return show [OPTIONS] {doc}
```

**Arguments**:

* `doc`: Document id of a 1040  [required]

**Options**:

* `--help`: Show this message and exit.

### `taxbinder return add-listed`

Checklist rows for what the return names that has none (its own year: missing only).

**Usage**:

```console
$ taxbinder return add-listed [OPTIONS] {doc}
```

**Arguments**:

* `doc`: Document id of a 1040  [required]

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder settings`

The app&#x27;s settings.

**Usage**:

```console
$ taxbinder settings [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `show`: Every setting, with what it is now (worked...
* `set`: Change one setting (undoable).

### `taxbinder settings show`

Every setting, with what it is now (worked out when nobody has set it).

**Usage**:

```console
$ taxbinder settings show [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder settings set`

Change one setting (undoable).

**Usage**:

```console
$ taxbinder settings set [OPTIONS] {key} {value}
```

**Arguments**:

* `key`: other_filers, tick_documents, websites_open_in, usual_box.&lt;form&gt;  [required]
* `value`: on/off, app_window/browser, or a box  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder household`

The household.

**Usage**:

```console
$ taxbinder household [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `show`: The household: confirmed, or worked out...
* `confirm`: Save the household as `household show` has...

### `taxbinder household show`

The household: confirmed, or worked out from the return or the documents.

**Usage**:

```console
$ taxbinder household show [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--help`: Show this message and exit.

### `taxbinder household confirm`

Save the household as `household show` has it (change it in the app first if needed).

**Usage**:

```console
$ taxbinder household confirm [OPTIONS]
```

**Options**:

* `--year <int>`: Tax year  [default: (last year)]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder document`

Whose a document is; moving it.

**Usage**:

```console
$ taxbinder document [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `whose`: Say whose a document is: one person, both,...
* `move`: Move a document into another filer&#x27;s vault...

### `taxbinder document whose`

Say whose a document is: one person, both, or a trust.

**Usage**:

```console
$ taxbinder document whose [OPTIONS] {doc} [taxpayers]...
```

**Arguments**:

* `doc`: Document id, e.g. doc_0123456789ab  [required]
* `taxpayers...`: Taxpayer ids (both: two)

**Options**:

* `--new-trust / --no-new-trust`: The trust named on it becomes known  [default: no-new-trust]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder document move`

Move a document into another filer&#x27;s vault (out of this one).

**Usage**:

```console
$ taxbinder document move [OPTIONS] {doc}
```

**Arguments**:

* `doc`: Document id  [required]

**Options**:

* `--to <str>`: The other filer&#x27;s slug (see `taxbinder entity list`)  [required]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

## `taxbinder entity`

Entities that file their own return.

**Usage**:

```console
$ taxbinder entity [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add an entity: a new vault beside the...
* `rename`: Rename the entity chosen with --entity...
* `list`: The household and every entity.

### `taxbinder entity add`

Add an entity: a new vault beside the household&#x27;s, opened by the same keys.

**Usage**:

```console
$ taxbinder entity add [OPTIONS] {name}
```

**Arguments**:

* `name`: e.g. &#x27;Northwind Holdings Inc.&#x27;  [required]

**Options**:

* `--form <str>`: 1120, 1120-S, 1065, 1041 or 1040  [default: 1120]
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder entity rename`

Rename the entity chosen with --entity (the household by default).

**Usage**:

```console
$ taxbinder entity rename [OPTIONS] {name}
```

**Arguments**:

* `name`: New name, e.g. &#x27;Quill Family&#x27;  [required]

**Options**:

* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder entity list`

The household and every entity.

**Usage**:

```console
$ taxbinder entity list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `taxbinder bill`

Bills and scheduled payments.

**Usage**:

```console
$ taxbinder bill [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a bill: one planned payment per...
* `list`: Planned and scheduled payments, soonest...
* `schedule`: Record that a payment is scheduled with...
* `paid`: Mark a planned payment paid (then `link`...

### `taxbinder bill add`

Add a bill: one planned payment per installment (TaxBinder never pays).

**Usage**:

```console
$ taxbinder bill add [OPTIONS] {payee}
```

**Arguments**:

* `payee`: Who it&#x27;s paid to  [required]

**Options**:

* `--installment <str>`: AMOUNT@YYYY-MM-DD[:label], repeatable  [required]
* `--kind <str>`: property_tax, vehicle_fee, tax_payment, bill  [default: bill]
* `--jurisdiction <str>`: For tax_payment: federal, CA …
* `--payment-kind <str>`: For tax_payment: estimated_q4 …
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder bill list`

Planned and scheduled payments, soonest first.

**Usage**:

```console
$ taxbinder bill list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `taxbinder bill schedule`

Record that a payment is scheduled with your bank or the payee.

**Usage**:

```console
$ taxbinder bill schedule [OPTIONS] {claim}
```

**Arguments**:

* `claim`: Payment id, from `bill list`  [required]

**Options**:

* `--on <str>`: Date it&#x27;s scheduled for  [required]
* `--note <str>`: e.g. &#x27;Chase bill pay&#x27;
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.

### `taxbinder bill paid`

Mark a planned payment paid (then `link` the confirmation to it).

**Usage**:

```console
$ taxbinder bill paid [OPTIONS] {claim}
```

**Arguments**:

* `claim`: Payment id, from `bill list`  [required]

**Options**:

* `--on <str>`: Date it was paid  [required]
* `--amount <str>`: If different from planned
* `--actor <str>`: Recorded as the author of the change  [default: (your login name)]
* `--help`: Show this message and exit.
