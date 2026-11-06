# Using TaxBinder

This guide follows a year in the app, from the first run to handing your preparer their files.
TaxBinder's own Help (in the top bar) covers the same ground in shorter form, with the paths to
your data on your Mac.

TaxBinder doesn't give tax advice and doesn't prepare or file returns: your preparer is the one
to ask.

## The first run

Open TaxBinder. The first screen offers:

- **Get started**: creates your binder. There's nothing to fill in; its key goes into the macOS
  Keychain. The first time, macOS asks whether `taxbinder-backend` may use the
  "com.4ampersand.taxbinder" Keychain item: choose **Always Allow**.
- **Try with sample data**: opens the demo, an invented family's binder with their documents
  and last year's return. It's kept apart from yours: never backed up, never shared, and nothing
  of yours is shown or changed. The bar at the top leaves it.
- **Used TaxBinder before?**: restore from a backup, unlock with your recovery key, or move from
  another Mac (below).

On a computer with less than 16 GB of memory, the first screen says that TaxBinder reads with
its rules alone. Everything else works the same; you'll confirm more by hand.

## Your binder

Home is **Your binder**: a deck of cards, one per tax year, the year you're working on in front.
Down a card are the senders of your documents, across it the kinds of document. Each square is
one sender's documents of one kind that year, and the key under the deck says what its look
means: in your binder, to come, never arrived, or came the year before but isn't on this year's
list.

- Click a year's tab to bring its card forward.
- Click a square to see it beside the deck: view the document, type or correct its amount, pick
  which box to follow, add a comment or the sender's website, or mark it not expected this year
  with a short reason (so you remember why next year).
- **Add to this year's list** adds a document still to come that the binder doesn't know yet.
- Beside the deck, for the year in front: what's still to come, the year's checks, whether it's
  ready for your preparer, and **Noticed for you**: things TaxBinder worked out from your
  earlier years (a sender that came every year but not this one, a new sender, a big change).
  Each is answered with one click.
- **Returns**, beside the deck: the year's federal return and one for each state you live in
  that has an income tax. Click **Change** to say where each is (not started, with your
  preparer, filed, accepted, or not needed), whether an extension was filed, and what it came
  to: a refund (and when it arrived) or an amount owed (paid once its balance-due payment is on
  Payments). The federal return shows its deadline, the extended one after an extension, and
  Check reminds you as it gets close. TaxBinder doesn't know the states' deadlines yet.
  Marking a return's PDF **Filed copy** (under Documents) marks that return Filed too.
  Across the years has a table of every year's returns.
- **Start next year** and **An earlier year** sit above the deck. A new year carries over the
  rows of the one it starts from; statuses start fresh.

## Adding documents

**Add documents** takes PDFs, scans and photos (JPEG, PNG, HEIC), one at a time or a whole
folder. Duplicates are skipped. Each file goes through the same steps on your computer, shown as
they happen: its words read, what it is, who sent it, whose it is and its year, its amounts read
by the rules and again by the AI model, then the rule book's checks.

When the last one is done, whatever TaxBinder wasn't sure of is asked right there: what a
document is, its year, who sent it, whose it is, two copies of one form, or an amount the two
readings didn't agree on. **Looks good, file them** puts them in your binder.

Only the amounts that matter are asked about: the box a document's row follows in Across the
years; for a W-2, boxes 1, 2, 16 and 17; for a consolidated 1099, ordinary dividends, interest,
capital gain distributions and the net gain or loss. The reading bench beside the steps shows
every box that was read.

Below that, **What doesn't come in the mail** asks about donations, vehicle fees, property tax,
estimated payments and accounts abroad, each with "None this year".

Documents also come in through the **inbox folder** (Settings › Advanced shows it) and through
**institution websites** opened from TaxBinder, where whatever you save is filed. Their
questions wait on **Check**.

## Check

**Check** (in the top bar, with a count when something's waiting) starts with what TaxBinder
worked out about your household from last year's return and your documents: people, filing
status and state, with **Looks right** or **Change**. Then the questions it couldn't answer
itself. Everything it was sure of is folded under **Looks right**, by sender, so nothing is filed
unseen but nothing has to be read.

You can change a document's kind, year, sender, account or whose it is, mark it "not a tax
document", or skip it. Each answer is one change you can undo in **Changes**.

## Payments, donations, accounts abroad

- **Payments**: estimated tax, property tax and other bills, paid or scheduled, with reminders.
  The **Estimated tax due dates** list shows the IRS's dates and your state's. Dates nobody has
  checked against the state's own pages say "not reviewed".
- **Donations**: each gift with the letter and records it needs; it stays open until they're
  linked.
- **Foreign accounts**: each account abroad with its highest balance, interest and year-end
  exchange rate; each person's total in dollars against the $10,000 FBAR line (a joint account
  counts in full for each owner). A fact about the accounts, not advice.
- **For my preparer** (on each year's card): papers no form covers, such as a letter or a
  closing statement, each with a note saying what it's for. TaxBinder keeps them as they are,
  never reads or checks them, and puts them in your preparer's files under your notes.

## Across the years

**Across the years** is the binder as a table: one row per sender's document, one column per
year, with its key amount. Amounts are typed in place; a row can follow another box (a K-1's
box 1 or box 2, for example), and that changes it in every year. A big change from the year
before (at least 25% and $500, or a change of sign) is marked. **Download as a spreadsheet** saves
it as an Excel file.

## Seeing a document

**View** opens a document beside the page, so you can type or answer while you read. From an
amount in Across the years it opens at the page the amount came from. What you see are pictures
of the pages drawn on your computer, not the file itself, so nothing in a file can run in
TaxBinder.

## Text for an AI chat

To ask an AI chat about your documents, select them in **Documents** and choose **Prepare for
chat**. TaxBinder replaces names, SSNs, EINs, account numbers and anything you list under
**Privacy** with placeholders like `[PERSON_02]`; the same value always gets the same
placeholder. If anything it knows is left over, it produces nothing. You paste the text into the
chat yourself; TaxBinder sends nothing. **Translate an answer** on the Privacy page turns the
placeholders in the reply back into the real values, on screen only.

Scans and photos can't be prepared this way yet.

## Files for your preparer

**Files for your preparer** says whether the year is ready and why, then makes one .zip in your
Downloads folder: a summary (who's filing, what's here, what's still to come, what needs a
look), the documents in folders by type, your payments and donations as lists, and the papers
for your preparer. Account numbers show only their last four digits.

The .zip isn't encrypted: anyone with the file can open your documents. Give it to your preparer
through their secure portal, then delete it from Downloads. When documents arrive after you made
it, the page says it's out of date.

## Keep it safe: the recovery key and backups

**Keep it safe** (also from **Last backup** in the top bar) has two steps:

1. **Your recovery key.** A line of text that opens your binder and your backups. Print it, save
   it as a PDF or copy it into a password manager, then type its last four characters to
   confirm. TaxBinder doesn't keep it.
2. **A backup.** **Back up now** saves one encrypted file (it ends in `.tbak`) with every
   binder, the household's and each filer's, with all documents, records and history. It's safe
   to keep in a cloud drive.

What brings back what:

| You have | It brings back |
|---|---|
| A backup and the recovery key (or this Mac's key) | Everything, as it was when the backup was made, on this Mac or another |
| The recovery key alone | A binder still on the Mac whose key was lost; not a binder that's gone |
| A backup alone, no key | Nothing. Nobody can open it, not even TaxBinder |
| Undo, in Changes | A change made by mistake |

### Moving to a new Mac

1. On the old Mac, **Back up now**.
2. Have your recovery key at hand. (Lost it? Keep it safe can show the old Mac's key, which
   opens the backup just the same.)
3. Copy the `.tbak` file to the new Mac: AirDrop, iCloud Drive or a USB drive.
4. On the new Mac, open TaxBinder, choose **Restore from a backup**, pick the file and type the
   key.
5. Sign in again to the institution websites you use from TaxBinder.

## Trusts and businesses

A trust or business that files its own return gets a binder of its own. Turn on **Other filers**
in Settings › Advanced; a switcher at the top then picks which one you're working on, and **＋**
adds one. A document addressed to a trust is pointed out on Check, and moving it to that
trust's binder is offered, never done by itself.

## Sharing at home

**Home sharing** (Settings › Advanced) lets another device on your home network use TaxBinder in a browser
while the app is open. It's off until you turn it on. The other device opens the address shown,
accepts the certificate (compare its fingerprint with the one shown), and enters a one-time
pairing code. Paired devices can be revoked at any time. Settings, sharing and the binder's keys
can only be changed on the Mac itself.

## Settings

- **Household** and **Years**: who's in the household; each year's filing status and state (a
  move is two states and its date).
- **Backups**: when the last one was made.
- **Reading with an AI model**: download a model (its size is shown first; it comes from Hugging
  Face and is checked before use), switch between models, or turn reading with the model off.
- **Advanced**: other filers; ticking documents one by one against the paper; how institution
  websites open (in an app window or your browser); the inbox folder; Privacy (values to hide
  from AI-chat text); home sharing; the usual box each kind of form follows in Across the
  years.

Larger text: View › Make Text Bigger (⌘+) in TaxBinder's window. Everything works from the
keyboard, and Reduce motion (in the Mac's Accessibility settings) is respected.

## Privacy

Everything TaxBinder keeps is on your computer, in your binder's folder.

**Encrypted**: every document file; the text read from scans and what the AI model read;
account numbers, routing numbers, SSNs, EINs and payment confirmation numbers; the values you
list to hide from AI-chat text; backups, whole.

**Not encrypted**: names of people, institutions and charities; amounts; dates; what each
document is; your checklist and notes; what's read from a tax return apart from SSNs. These are
plain files in your binder's folder, protected by your Mac's login and FileVault. Also not
encrypted: files waiting in the inbox folder, and whatever you make to use elsewhere (the .zip,
the Excel file, saved documents, text copied for an AI chat).

**Over the network**, only when you choose it: institution websites you open from TaxBinder;
home sharing; printing; downloading an AI model (from Hugging Face; nothing about you is sent);
and whatever you save and send yourself. Also, when TaxBinder opens (at most once a day), it asks
GitHub which version is the latest, with nothing about you; Settings › About turns that off.

## Updating

When there's a newer version, Your binder and **Settings › About** say so. **Download** opens its
page in your browser: open the downloaded file and drag TaxBinder into Applications, replacing
the old one. Your binders stay as they are. **Check now** asks straight away.

## Uninstalling

Moving TaxBinder to the Trash removes the app only. Your binders, the key in the Keychain,
website sign-ins and your backups stay, so installing it again finds everything as it was.
Help › Uninstalling lists the exact folders. To remove everything, first make a backup and keep
it with your recovery key, then delete those folders and the "com.4ampersand.taxbinder" entries
in Keychain Access. That can't be undone.

## Reporting a problem

Help › Report a problem shows diagnostic info to copy into an email: versions, how many
documents and filers, how often you corrected what was read, and recent warnings and errors.
It holds no names, amounts, account numbers or file names, and what you see is exactly what's
copied.
