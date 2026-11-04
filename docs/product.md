# TaxBinder: the product

## Who it's for

A US household that receives many tax documents (W-2s, 1099s, K-1s, 1098s, donation letters)
and hands them to a tax preparer. Also a trust or a small business that files its own return.

It's for people comfortable installing a Mac app. Nobody needs Terminal, Git or a spreadsheet to
use it.

It isn't for preparing or filing returns, for tax advice, or for preparers managing many clients.

## The problem

A household with a few brokerage accounts, some partnership K-1s, a trust and a handful of
charitable gifts gets dozens of documents a year, from January into the autumn. Keeping track
usually means a spreadsheet and a folder:

- which documents should come this year, and which are still missing;
- whether a corrected form replaced one already sent to the preparer;
- whether each donation has the letter it needs, and each estimated payment was on time;
- whether this year's amounts look like last year's.

TaxBinder replaces the spreadsheet and the folder with a binder that fills itself in as documents
arrive, and a rule book that says what's missing, what disagrees and what needs a look.

## The ideas you meet in the app

- **Binder.** Everything TaxBinder keeps for one filer: documents, records and the history of
  every change, in one encrypted folder on your computer (the *vault*).
- **Filer.** The household, or a trust or business that files its own return. Each has its own
  binder. Other filers are off until turned on in Settings.
- **Tax year.** Each year is a card in the deck on the home page. A document goes into the year
  printed on it, whichever year you're looking at.
- **Sender.** Whoever issued a document: an employer, a bank, a partnership. A sender is known
  by its EIN, so two funds with similar names stay apart and a renamed fund stays one sender.
- **Checklist row.** A document the binder expects from a sender in a year (a 1099-DIV from a
  brokerage account, a K-1 from a partnership). Each row has four separate statuses: whether it
  applies this year, whether it's here, whether you've checked it, and whether your preparer has
  it. Receiving a document doesn't mark it checked; checking it doesn't mark it sent.
- **Document.** A file in the binder: a PDF, scan or photo, read on your computer and linked to
  its row. A corrected form keeps the original beside it.
- **Payment, donation, bill, foreign account.** Things that matter at tax time and don't arrive
  as a form. Each one lists the evidence it needs (a confirmation, an acknowledgment letter, a
  statement) and stays open until that evidence is linked.
- **Finding.** What a check found: something missing, something that disagrees, or something
  to look at. A finding can be acknowledged with a note, and a requirement waived with a reason.
- **Change.** Every write is one change in the binder's history, with who made it. Any change
  can be undone.

## What it does

- **Knows what to expect.** Start a year from the one before: its rows carry over, statuses
  start fresh. Last year's return, if you add it, suggests the household, the state and the
  senders it names.
- **Reads documents on your computer.** PDFs, scans and photos (PDF, JPEG, PNG, HEIC). For
  each: its kind, tax year, sender and EIN, account (last 4 only), whose it is, and its key
  amounts. Scans and photos are recognised on the computer.
- **Reads every form twice.** With rules (the form's layout and arithmetic) and, on a computer
  with 16 GB of memory, an AI model that runs on that computer, downloaded once after asking.
  A key box the two don't agree on is shown to confirm. The model reads; the rules decide.
- **Asks, never guesses.** Anything unclear (whose a document is, its year, two copies of one
  form) is a question on Check or on Add documents.
- **Checks.** Deterministic rules with parameters per tax year: deadlines, estimated payments,
  donation substantiation, foreign-account totals, duplicate and corrected documents. A year
  without parameters isn't checked, and the app says so rather than showing "all clear".
- **Shows the years side by side.** Across the years: each document's key amount in every year,
  with big changes marked as facts, not advice.
- **Hands off.** The files for your preparer: one .zip per filer and year, with documents by
  type, payments and donations, open items and a summary. TaxBinder never sends it anywhere.
- **Keeps it safe.** Encrypted binder, a recovery key, one-file encrypted backups, undo for
  every change.

## What it deliberately doesn't do

- Prepare, file or check returns, or give tax advice.
- Send anything anywhere by itself. There's no account, no cloud service, no usage tracking, no
  crash reporting, and no AI service on the internet. The only AI model runs on your computer.
- Decide with the AI model. Its readings are checked by rules before they're used, and every
  check is plain, deterministic code.
- Move a document to another filer's binder by itself. It offers; you decide.
- Show a full account number, SSN or EIN anywhere in its own pages. They show as `…1234`.

## Not there yet

- Windows and Intel Macs.
- Signed, notarized downloads and automatic updates.
- Redacting scans and photos for an AI chat (reading them works).
- Filers with fiscal or short tax years. Documents from fiscal-year partnerships are handled.
- Reviewed estimated-payment dates for most states: the dates are there for every state and DC,
  labelled "not reviewed" until someone checks them.
