"""Redaction for LLM use (§9): documents → redacted Markdown with vault-wide placeholders."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from taxbinder.redact.detect import known_finders, pattern_finders
from taxbinder.redact.markdown import Leak, Redacted, redact_pdf_to_markdown
from taxbinder.redact.tokens import Pseudonyms, unredact
from taxbinder.store import RecordStore

__all__ = ["Bundle", "Leak", "Redacted", "bundle", "unredact"]

INTRO = """The documents below are tax documents with personal details replaced by placeholders
in square brackets, such as [TIN_01] (a taxpayer ID), [EIN_02] (an employer ID), [ACCT_03] (an
account), [PERSON_01] (a person), [ADDR_01] (a street address). The same placeholder always
means the same thing, in every document. Please use the placeholders as they are in your
answers, and cite pages as "Document N, page P".
"""


@dataclass
class Bundle:
    markdown: str
    documents: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (doc id, why)
    scanned: list[tuple[str, list[int]]] = field(default_factory=list)  # (doc id, pages left out)


# redacting scans comes after the first release
SCANNED = "read from a scan or photo: it can't be checked for personal details yet, so it stays out"


def bundle(store: RecordStore, doc_ids: list[str], actor: str) -> Bundle:
    """Several documents as one redacted Markdown text. Documents that can't be redacted
    safely are left out and listed in `refused`. New placeholders are saved in the vault."""
    from taxbinder.doctypes import labels

    names = labels(store)
    tokens = Pseudonyms(store)
    finders = known_finders(store, tokens) + pattern_finders()
    out = Bundle(markdown=INTRO)
    n = 0
    for doc_id in doc_ids:
        doc, _ = store.get("document", doc_id)
        if doc.mime != "application/pdf" or doc.scanned:
            out.refused.append((doc_id, SCANNED))
            continue
        try:
            r = redact_pdf_to_markdown(store.blobs.get(doc.sha256), finders, tokens)
        except (Leak, ValueError) as e:
            out.refused.append((doc_id, str(e)))
            continue
        if not r.markdown.strip():
            out.refused.append((doc_id, SCANNED))
            continue
        n += 1
        c = doc.classification
        what = names.get(c.doc_type, c.doc_type) if c and c.doc_type else "Document"
        year = f", tax year {c.tax_year}" if c and c.tax_year else ""
        out.markdown += f"\n\n---\n\n## Document {n}: {what}{year}\n\n{r.markdown}"
        out.counts.update(r.counts)
        if r.scanned_pages:
            out.scanned.append((doc_id, r.scanned_pages))
    out.documents = n
    tokens.save(store, actor)
    return out
