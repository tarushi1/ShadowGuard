"""
Tests for POST /api/v1/analyze/image.
================================================================

This endpoint accepts arbitrary files from a browser, which makes its
input validation a security surface rather than a convenience. Roughly
half the tests here are about what it REFUSES.
"""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from backend.app.db import session
from backend.app.detectors import ocr
from backend.app.main import app
from backend.app.models.event import DecisionEvent

client = TestClient(app)
ENDPOINT = "/api/v1/analyze/image"

needs_tesseract = pytest.mark.skipif(
    not ocr.available(), reason="tesseract binary not installed"
)


def post(image: bytes, filename: str = "shot.png", **form):
    data = {"user_id": "test-user", "platform": "chatgpt", **form}
    return client.post(ENDPOINT, files={"file": (filename, image, "image/png")}, data=data)


def shot(text: str) -> bytes:
    from evaluation.render import render

    return render(text)


# ---------------------------------------------------------------
# The point of Stage 3
# ---------------------------------------------------------------

@needs_tesseract
def test_a_secret_in_an_image_is_caught():
    """An image is a door into the same pipeline, not a separate
    detection problem — so a key in a screenshot must be stopped the
    same way a typed one is."""
    body = post(shot("my deploy key is AKIAIOSFODNN7EXAMPLE, rotate it")).json()

    assert body["action"] == "block"
    assert "AWS_ACCESS_KEY_ID" in {e["type"] for e in body["entities"]}


@needs_tesseract
def test_stages_run_shows_ocr_ran_first():
    """`stages_run` is what makes the pipeline's shape evidence rather
    than a claim."""
    body = post(shot("my deploy key is AKIAIOSFODNN7EXAMPLE")).json()

    assert body["stages_run"][0] == "ocr"


@needs_tesseract
def test_an_ordinary_screenshot_is_allowed():
    body = post(shot("how do I reverse a string in python")).json()

    assert body["action"] == "allow"


@needs_tesseract
def test_an_image_with_no_text_is_allowed_but_reports_low_confidence():
    """An unreadable image is an ABSENCE OF EVIDENCE, not a pass.

    `allow` is the right action, and the confidence travels back so the
    user can tell "we read this and it was fine" from "we could not
    read this". Collapsing those two is how a blurry screenshot becomes
    a bypass.
    """
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (400, 200), (255, 255, 255)).save(buffer, format="PNG")

    body = post(buffer.getvalue()).json()

    assert body["action"] == "allow"
    assert body["ocr"]["confidence"] == 0.0
    assert body["ocr"]["characters"] == 0


# ---------------------------------------------------------------
# The privacy properties
# ---------------------------------------------------------------

@needs_tesseract
def test_the_response_never_returns_the_extracted_text():
    """`ocr` describes what was read without quoting it. `characters`
    says how much; nothing says what."""
    import json

    secret = "AKIAIOSFODNN7EXAMPLE"
    body = post(shot(f"deploy key {secret} rotate now")).json()

    assert body["ocr"]["characters"] > 0
    assert secret not in json.dumps(body)


@needs_tesseract
def test_redaction_is_never_offered_for_an_image():
    """The risk engine works on text and will happily say `redact`,
    because from its point of view the secret sits in a sentence that
    survives its removal. It does — but the user is holding a PNG.

    Emitting an action the client cannot perform is the exact failure
    D35 warns about. A card in an image, which redacts as text, must
    come back as `warn` instead.
    """
    body = post(shot("charge 4532015112830366 for the invoice please")).json()

    assert body["action"] in {"warn", "block"}
    assert body["redacted_text"] is None


@needs_tesseract
def test_a_critical_secret_in_an_image_still_blocks():
    """The other half of the rule above.

    Downgrading every `redact` to `warn` would make the image path
    weaker than the text path and reward exactly the bypass B11
    describes. The score decides, using the same threshold as
    everything else.
    """
    body = post(shot("my deploy key is AKIAIOSFODNN7EXAMPLE, rotate it")).json()

    assert body["action"] == "block"


@needs_tesseract
def test_the_image_is_never_written_to_disk():
    """The bytes go from the request into memory, through Tesseract,
    and out of scope. Nothing under data/ should appear."""
    from pathlib import Path

    root = Path(session.database_url().removeprefix("sqlite:///")).parent
    before = set(root.glob("**/*")) if root.exists() else set()

    post(shot("deploy key AKIAIOSFODNN7EXAMPLE"))

    after = set(root.glob("**/*")) if root.exists() else set()
    new_images = {p for p in after - before if p.suffix in {".png", ".jpg", ".jpeg", ".webp"}}

    assert not new_images, f"the image was persisted: {new_images}"


@needs_tesseract
def test_the_decision_records_image_metadata_and_nothing_more():
    post(shot("my deploy key is AKIAIOSFODNN7EXAMPLE"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.source == "image"
    assert event.image_width > 0
    assert event.ocr_words > 0
    assert 0.0 <= event.ocr_confidence <= 1.0


# ---------------------------------------------------------------
# Input validation — this accepts files from a browser
# ---------------------------------------------------------------

def test_a_non_image_is_rejected_with_422_not_500():
    """A malformed file must produce a clear refusal. A 500 here would
    mean an unhandled decoder error, and image decoders are a classic
    source of them."""
    response = post(b"this is not an image at all")

    assert response.status_code == 422


def test_an_empty_upload_is_rejected():
    assert post(b"").status_code == 422


def test_a_missing_user_id_is_rejected():
    from evaluation.render import render

    response = client.post(
        ENDPOINT, files={"file": ("s.png", render("hello"), "image/png")}, data={}
    )

    assert response.status_code == 422


def test_an_oversized_image_is_refused_with_413():
    """413 rather than 422: the request was well-formed, it was just
    too big, and the distinction tells a client whether retrying with
    the same file could ever work."""
    from backend.app.schemas.analyze import MAX_IMAGE_BYTES

    response = post(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1))

    assert response.status_code == 413


@needs_tesseract
def test_an_unsupported_format_is_refused():
    """A small allowlist rather than "whatever Pillow can open".

    Pillow supports dozens of formats, some with decoders that have had
    CVEs, and a DLP endpoint is an unusually attractive thing to feed a
    malformed file to.
    """
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (50, 50)).save(buffer, format="TIFF")

    response = post(buffer.getvalue(), filename="x.tiff")

    assert response.status_code == 422
    assert "format" in response.json()["detail"].lower()
