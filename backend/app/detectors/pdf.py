"""
Stage 3b — PDF as a text source.
================================================================

A second SOURCE, not a second detector. `ocr.py` established the
pattern (D41): a source turns some non-text artefact into text and
hands it to the pipeline that already exists. Nothing below this
module knows or cares that a PDF was involved, which is why every
improvement to Stages 1 and 2 improves PDF handling for free.

What makes a PDF worth its own module rather than a branch inside the
image path is that **"the text of a PDF" is not one thing.** Five
different places in the same file can hold a secret, and four of them
are invisible when the document is read on screen:

    page text        what a human sees
    metadata         Author, Title, Producer, Keywords -- routinely
                     carries real names, internal usernames and full
                     filesystem paths, put there by the software that
                     made the file rather than by the author
    annotations      reviewer comments, sticky notes, highlights. Often
                     the most candid text in a document, and never
                     printed
    form fields      filled-in values on an AcroForm. A completed
                     application form's PII lives here, NOT in the page
                     text
    hidden text      the important one -- see below

**Text under a redaction box.** Drawing a black rectangle over a
paragraph does not remove the paragraph. The characters remain in the
content stream, selectable and copyable, with an opaque shape painted
on top. This is not a hypothetical: courts, law firms and government
agencies have published "redacted" filings whose text could be
recovered by selecting it. Because this module reads the content
stream and not the rendered page, it sees exactly what the person who
drew the rectangle believed they had removed.

That is the single strongest argument for scanning PDFs at all. Every
other channel here catches a secret its sender knew was there. This
one catches a secret its sender is certain they have already removed.

**Where this is stronger than OCR, and why that is measurable.** A
born-digital PDF carries a real text layer, so extraction is exact --
no recognition, no confidence, no character confusion. `GOOGLE_API_KEY`
and `JWT` survive OCR 0% of the time ([B11](../../../docs/security.md)),
so the same key is caught in a PDF and missed in a screenshot of that
PDF. The PDF path is the stronger channel, and the evaluation should
say so with numbers rather than by assertion.

**Where it is blind, stated up front rather than discovered.** A
scanned PDF is a photograph in a wrapper: no text layer, nothing to
extract. This module does NOT rasterise pages, so such a document
yields nothing. It must therefore never be reported as clean --
`has_text_layer` exists precisely so the caller can tell "we read this
and it was fine" apart from "there was nothing here to read". Treating
those as the same thing is how a scanned payslip becomes a bypass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO

log = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Limits
# ---------------------------------------------------------------
# A PDF parser is an interpreter for a Turing-complete-ish document
# format, fed bytes from outside. Every limit below exists because the
# alternative is a denial of service against our own backend, and a
# security tool that can be switched off by sending it a file is worse
# than no tool at all.

MAX_PAGES = 200
"""Pages read before the rest are ignored.

Not a rejection. A 5,000-page document is far more likely to be a
genuine export than an attack, and refusing it outright would push the
user to send it unchecked. Reading the first 200 and SAYING SO is the
honest failure: `truncated` travels back with the result.
"""

MAX_EXTRACTED_CHARS = 2_000_000
"""Roughly 400,000 words. Guards the case a page count cannot: a
handful of pages whose content streams decompress into hundreds of
megabytes of text. Pages are cheap to count and expensive to trust."""

MAX_ANNOTATIONS = 5_000
"""Annotations are unbounded in the format and cheap to generate."""


@dataclass(frozen=True)
class PdfResult:
    """What a PDF turned out to say."""

    text: str
    """Everything extracted, joined. Passed to Stages 1 and 2, never stored."""

    page_count: int
    pages_read: int

    has_text_layer: bool
    """False for a scanned document.

    The most important field here, and the reason it is not a private
    detail. False means THIS MODULE FOUND NOTHING TO READ, which is an
    absence of evidence and not evidence of absence. A caller that maps
    an empty extraction straight to `allow` has built a bypass: put the
    secret in a photograph, wrap it in a PDF, send it.
    """

    truncated: bool = False
    """True when the document was longer than `MAX_PAGES`."""

    sources: tuple[str, ...] = ()
    """Which of the five channels actually yielded text.

    Kept because it is the only way to answer "is reading annotations
    worth the code?" with data rather than intuition — the same reason
    `OcrResult` keeps `raw_text` alongside `text`.
    """

    encrypted: bool = False
    """True when the file is password-protected and could not be read."""

    char_count: int = field(default=0)


def available() -> bool:
    """Is the PDF backend importable?

    Unlike Tesseract this is a pure-Python dependency, so it cannot be
    installed-but-broken in the way a missing system binary can. The
    function exists anyway so the caller has one shape to program
    against across both sources, and so a stripped install degrades
    Stage 3b rather than failing at import.
    """
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def _metadata_text(reader) -> str:
    """Author, Title, Subject, Keywords, Creator, Producer.

    Deliberately NOT every key in the dictionary. A PDF may carry
    arbitrary custom metadata, and hoovering all of it up would feed
    the detectors long runs of tool version strings and timestamps —
    high-entropy noise that costs precision for no recall. These six
    are the ones that hold human-written values.
    """
    info = getattr(reader, "metadata", None)
    if not info:
        return ""

    wanted = ("/Author", "/Title", "/Subject", "/Keywords", "/Creator", "/Producer")
    parts = []

    for key in wanted:
        try:
            value = info.get(key)
        except Exception:  # noqa: BLE001 - malformed metadata must not be fatal
            continue
        if value:
            parts.append(f"{key.lstrip('/')}: {value}")

    return "\n".join(parts)


def _annotation_text(page, budget: int) -> tuple[str, int]:
    """Comments, sticky notes and highlight text on one page.

    Returns the text and the remaining budget. Annotations are
    unbounded in the format, so the cap is enforced across the whole
    document rather than per page — a thousand pages with five
    annotations each is the same risk as one page with five thousand.
    """
    if budget <= 0:
        return "", budget

    try:
        annots = page.get("/Annots")
        if not annots:
            return "", budget
        annots = annots.get_object()
    except Exception:  # noqa: BLE001
        return "", budget

    parts = []
    for annot in annots:
        if budget <= 0:
            break
        try:
            obj = annot.get_object()
            # /Contents is the note body. /T is its author, which is a
            # person's name and therefore PII in its own right.
            for key in ("/Contents", "/T"):
                value = obj.get(key)
                if value:
                    parts.append(str(value))
            budget -= 1
        except Exception:  # noqa: BLE001
            continue

    return "\n".join(parts), budget


def _form_text(reader) -> str:
    """Values typed into an AcroForm.

    A completed form's PII is here and NOT in the page text: the page
    draws the boxes, the field dictionary holds what was written in
    them. Extracting only page text on a filled-in application form
    reads the questions and misses every answer.
    """
    try:
        fields = reader.get_fields()
    except Exception:  # noqa: BLE001
        return ""

    if not fields:
        return ""

    parts = []
    for name, field_obj in fields.items():
        try:
            value = field_obj.get("/V")
        except Exception:  # noqa: BLE001
            continue
        if value:
            parts.append(f"{name}: {value}")

    return "\n".join(parts)


def extract_text(pdf_bytes: bytes) -> PdfResult:
    """Read everything readable out of a PDF.

    Pure with respect to the filesystem: nothing is written, and the
    parsed document exists only for the duration of this call.

    Broad exception handling throughout, and it is deliberate rather
    than lazy. This function parses hostile input in the same process
    that serves the API. A malformed annotation dictionary must cost
    that annotation, not the document — and never the process. The
    failure mode that matters is not "this PDF was unusual", it is
    "one unusual PDF stopped ShadowGuard inspecting anything at all".

    :param pdf_bytes: the raw upload
    :raises ValueError: when the file is not a readable PDF
    :raises RuntimeError: when the PDF backend is not installed
    """
    if not available():
        raise RuntimeError("pypdf is not installed — `pip install pypdf`")

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except PdfReadError as error:
        raise ValueError("not a readable PDF") from error
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"could not open the PDF: {error}") from error

    # ---- Encrypted -----------------------------------------------
    # An empty-password PDF is common (encrypted for permissions, not
    # secrecy) and pypdf opens it with an explicit decrypt call. A
    # genuinely password-protected one cannot be read, and that is
    # reported rather than silently returning no text — an unreadable
    # document is not a safe one.
    if getattr(reader, "is_encrypted", False):
        try:
            if reader.decrypt("") == 0:
                return PdfResult(
                    text="", page_count=0, pages_read=0,
                    has_text_layer=False, encrypted=True,
                )
        except Exception:  # noqa: BLE001
            return PdfResult(
                text="", page_count=0, pages_read=0,
                has_text_layer=False, encrypted=True,
            )

    try:
        page_count = len(reader.pages)
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"could not read the page list: {error}") from error

    pages_read = min(page_count, MAX_PAGES)

    chunks: list[str] = []
    sources: list[str] = []
    total = 0

    # ---- The four document-level channels ------------------------
    metadata = _metadata_text(reader)
    if metadata:
        chunks.append(metadata)
        sources.append("metadata")
        total += len(metadata)

    forms = _form_text(reader)
    if forms:
        chunks.append(forms)
        sources.append("forms")
        total += len(forms)

    # ---- Page text and annotations -------------------------------
    page_text_found = False
    annotation_budget = MAX_ANNOTATIONS
    annotations_found = False

    for index in range(pages_read):
        if total >= MAX_EXTRACTED_CHARS:
            break

        try:
            page = reader.pages[index]
        except Exception:  # noqa: BLE001
            log.debug("page %d could not be loaded", index)
            continue

        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            log.debug("page %d text could not be extracted", index)
            text = ""

        if text.strip():
            page_text_found = True
            chunks.append(text)
            total += len(text)

        notes, annotation_budget = _annotation_text(page, annotation_budget)
        if notes.strip():
            annotations_found = True
            chunks.append(notes)
            total += len(notes)

    if page_text_found:
        sources.append("pages")
    if annotations_found:
        sources.append("annotations")

    joined = "\n".join(chunks)[:MAX_EXTRACTED_CHARS]

    return PdfResult(
        text=joined,
        page_count=page_count,
        pages_read=pages_read,
        # Keyed on PAGE text specifically, not on whether anything at
        # all was extracted. A scanned document with a populated Author
        # field has metadata and no text layer, and calling that "text
        # was found" would hide exactly the case this flag exists for.
        has_text_layer=page_text_found,
        truncated=page_count > pages_read,
        sources=tuple(sources),
        char_count=len(joined),
    )
