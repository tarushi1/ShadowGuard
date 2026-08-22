"""
Stage 3b — PDFs as a text source.
================================================================

Two properties are being defended here, and they pull in opposite
directions.

RECALL: a PDF holds text in five different places, four of which are
invisible when the document is read on screen. Extracting only what a
human sees would miss most of them, and the miss would be silent.

HONESTY: a scanned document has nothing to extract, and reporting that
silence as "clean" would build a one-step bypass into the system —
print it, scan it, attach it. `has_text_layer` exists so the absence of
evidence never gets rendered as evidence of absence, and several tests
here exist purely to hold that line.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.detectors import pdf
from backend.app.pipeline import orchestrator
from backend.app.db import events as event_store
from backend.app.main import app
from backend.tests.helpers import pdfs

client = TestClient(app)
ENDPOINT = "/api/v1/analyze/pdf"

AWS_KEY = "AKIA3RTFPQ2XMBNZ7KLW"


def post(data: bytes, name: str = "doc.pdf"):
    return client.post(
        ENDPOINT,
        files={"file": (name, data, "application/pdf")},
        data={"user_id": "pdf-test", "platform": "chatgpt"},
    )


# ---------------------------------------------------------------
# The five channels
# ---------------------------------------------------------------

def test_page_text_is_extracted():
    result = pdf.extract_text(pdfs.text_pdf(f"aws_access_key_id = {AWS_KEY}"))

    assert AWS_KEY in result.text
    assert result.has_text_layer is True
    assert "pages" in result.sources


def test_metadata_is_extracted():
    """Author, Title and Producer routinely carry real names and paths.

    Put there by the software that made the file rather than by the
    person who wrote it, which is exactly why nobody remembers to check
    them before sharing a document.
    """
    result = pdf.extract_text(
        pdfs.text_pdf("Quarterly figures.", metadata={"Author": "priya.menon@acme.com"})
    )

    assert "priya.menon@acme.com" in result.text
    assert "metadata" in result.sources


def test_annotations_are_extracted():
    """Reviewer comments are never printed and rarely reread."""
    result = pdf.extract_text(pdfs.annotated_pdf("client aadhaar 2345 6789 0123"))

    assert "2345 6789 0123" in result.text
    assert "annotations" in result.sources


def test_form_values_are_extracted():
    """The page draws the questions; the field dictionary holds the answers."""
    result = pdf.extract_text(
        pdfs.form_pdf(claimant="Priya Menon", ssn_field="123-45-6789")
    )

    assert "123-45-6789" in result.text
    assert "forms" in result.sources


def test_text_under_a_redaction_box_is_extracted():
    """The headline case, and the strongest argument for this module.

    Drawing a filled rectangle over a paragraph does not remove the
    paragraph. The characters stay in the content stream, in order,
    fully selectable, with an opaque shape painted on top afterwards.
    Courts and government agencies have published "redacted" filings
    that could be un-redacted by selecting the text.

    Every other channel ShadowGuard watches catches a secret whose
    sender knew it was there. This one catches a secret whose sender is
    certain they already removed it.
    """
    document = pdfs.redacted_pdf("the claimant's SSN is 123-45-6789")

    result = pdf.extract_text(document)
    assert "123-45-6789" in result.text

    # And it survives all the way to a decision, not just extraction.
    response, _ = orchestrator.analyze_pdf(document)
    assert response.action.value != "allow"
    assert "US_SSN" in {e.type for e in response.entities}


# ---------------------------------------------------------------
# Unreadable is not clean
# ---------------------------------------------------------------

def test_a_scan_is_reported_as_unread_not_as_clean():
    """The bypass this flag exists to close.

    A scanned PDF has no text layer, so nothing is extracted, so it
    scores zero and the action is `allow`. That verdict is CORRECT and
    the silence around it is dangerous: unremarked, "print it, scan it,
    attach it" defeats every rule in the system in one step.
    """
    response, extracted = orchestrator.analyze_pdf(pdfs.scanned_pdf())

    assert extracted.has_text_layer is False
    assert response.pdf.has_text_layer is False

    # The message must not read as reassurance.
    assert "NOT been checked" in response.message
    assert "no sensitive" not in response.message.lower()


def test_an_encrypted_pdf_says_so_rather_than_reporting_nothing():
    response, extracted = orchestrator.analyze_pdf(pdfs.encrypted_pdf())

    assert extracted.encrypted is True
    assert response.pdf.encrypted is True
    assert "password-protected" in response.message
    assert "NOT been checked" in response.message


def test_a_clean_document_is_described_differently_from_an_unread_one():
    """The two must never share a sentence.

    Collapsing "we read this and found nothing" into "we found nothing"
    is how a scanned payslip goes out under a reassuring message.
    """
    clean, _ = orchestrator.analyze_pdf(pdfs.text_pdf("lunch is at one o'clock"))
    unread, _ = orchestrator.analyze_pdf(pdfs.scanned_pdf())

    assert clean.action.value == "allow"
    assert unread.action.value == "allow"
    assert clean.message != unread.message
    assert "NOT been checked" not in clean.message


# ---------------------------------------------------------------
# Limits — a parser fed hostile input
# ---------------------------------------------------------------

def test_a_long_document_is_truncated_and_says_so():
    """Not rejected. A 5,000-page export is far likelier to be genuine
    than hostile, and refusing it outright pushes the user to send it
    unchecked. Reading the cap and reporting `truncated` is the honest
    failure."""
    result = pdf.extract_text(pdfs.many_pages_pdf(pdf.MAX_PAGES + 20))

    assert result.page_count == pdf.MAX_PAGES + 20
    assert result.pages_read == pdf.MAX_PAGES
    assert result.truncated is True


def test_extracted_text_is_capped():
    assert pdf.MAX_EXTRACTED_CHARS > 0

    result = pdf.extract_text(pdfs.many_pages_pdf(50))
    assert len(result.text) <= pdf.MAX_EXTRACTED_CHARS


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not a pdf at all",
        b"%PDF-1.7\nbut then nothing useful follows",
        bytes(range(256)),
    ],
)
def test_malformed_input_raises_valueerror_rather_than_escaping(payload):
    """A parser reading hostile bytes in the process that serves the API.

    The failure that matters is not "this file was unusual" — it is
    "one unusual file stopped ShadowGuard inspecting anything at all".
    Every route out of this function must be a ValueError the endpoint
    turns into a 422, never an arbitrary exception and never a hang.
    """
    with pytest.raises(ValueError):
        pdf.extract_text(payload)


# ---------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------

def test_endpoint_returns_a_verdict():
    response = post(pdfs.text_pdf(f"aws_access_key_id = {AWS_KEY}"))

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "block"
    assert "AWS_ACCESS_KEY_ID" in {e["type"] for e in body["entities"]}
    assert body["pdf"]["has_text_layer"] is True


def test_endpoint_rejects_an_empty_upload():
    assert post(b"").status_code == 422


def test_endpoint_rejects_a_non_pdf():
    assert post(b"this is a text file").status_code == 422


def test_endpoint_reports_the_channels_it_read():
    response = post(pdfs.annotated_pdf("call 9876543210 about the claim"))

    assert response.status_code == 200
    assert "annotations" in response.json()["pdf"]["sources"]


# ---------------------------------------------------------------
# The property every source must hold
# ---------------------------------------------------------------

def test_the_document_never_reaches_the_database():
    """Same test as the one opening test_persistence.py, asked of a
    third input type. A PDF can carry far more sensitive material than
    a chat message, so the temptation to keep "just the extracted text,
    for debugging" is correspondingly larger."""
    from backend.tests.test_persistence import database_bytes

    secret = "zephyrine-quandt-9f2a-secret-lease"
    post(pdfs.text_pdf(f"the passphrase is {secret}"))

    stored = database_bytes()
    assert secret.encode() not in stored
    assert b"passphrase is" not in stored


def test_the_decision_is_recorded_as_a_pdf():
    """`source` is what lets the report answer "is the document path
    stronger than the image path?" with data instead of assertion."""
    post(pdfs.text_pdf(f"aws_access_key_id = {AWS_KEY}"))

    counts = event_store.source_counts()
    assert counts.get("pdf", 0) >= 1
