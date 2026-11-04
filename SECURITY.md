# Security

TaxBinder holds a household's tax documents, so security problems matter more than most bugs.

## Reporting a problem

Please don't open a public issue. Report it privately through GitHub's **Report a
vulnerability** (the repository's Security tab). Say what you found, how to reproduce it, and
which version. Never include real tax documents or real personal details: describe the problem
with invented ones.

Every report is read, and you'll get an answer. Once a fix is released, the report is
published with credit to you, unless you'd rather not be named.

## What's in scope

- Reading a vault, its documents or a backup without its key.
- Another program or web page on the same Mac reaching the app's local server, or anything on
  the network reaching it while sharing is off, or without pairing while it's on.
- A document's contents running as part of the app (a PDF, image or saved web page).
- An identifier (account number, SSN, EIN) leaking into logs, findings, file names, the
  diagnostic info, the files for the preparer or redacted text.
- Text prepared for an AI chat that still holds a value the vault knows.
- Anything sent over the network that the app's privacy statement doesn't list.

Out of scope: someone using the Mac as its own user, and the files for the preparer, which are
unencrypted by design (the app says so).

How the app protects against each of these is in
[docs/architecture.md](docs/architecture.md), sections 4 (the vault and its encryption), 9
(redaction) and 12 (security).
