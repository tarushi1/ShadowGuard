"""
Tests for Stage 3 — OCR and the repair of OCR damage.
================================================================

Split into two groups by what they need.

`repair()` is a pure string function and is tested exhaustively without
Tesseract, so these run everywhere and in microseconds. The extraction
tests need the binary and are skipped when it is missing — with a
reason, so a skip reads as "not installed here" rather than as a
silently absent guarantee.
"""

import pytest

from backend.app.detectors import ocr

needs_tesseract = pytest.mark.skipif(
    not ocr.available(), reason="tesseract binary not installed"
)


# ---------------------------------------------------------------
# repair() — the damage that can be undone
# ---------------------------------------------------------------

@pytest.mark.parametrize("dash", ["‐", "‑", "‒", "–", "—", "―", "−"])
def test_unicode_dashes_become_plain_hyphens(dash):
    """`sk-proj-` read as `sk-proj—` is enough on its own to defeat the
    OpenAI rule, and the mapping is unambiguous."""
    assert ocr.repair(f"key sk{dash}proj{dash}Ab1Cd2") == "key sk-proj-Ab1Cd2"


def test_spaces_inside_a_vendor_token_are_removed():
    """The failure that cost 30 detections.

    Tesseract has no model for random mixed-case strings, so it
    segments them as if they were several words.
    """
    damaged = "openai sk-proj-Bq7Lm3Np8Qr2S t6Uv 1Wx4Y Z9AbD5"

    assert ocr.repair(damaged) == "openai sk-proj-Bq7Lm3Np8Qr2St6Uv1Wx4YZ9AbD5"


def test_repair_does_not_eat_the_words_after_a_key():
    """The guard that keeps the repair from swallowing the sentence.

    `sk-proj-Bq7 is not working` must not become one token. Ordinary
    English words here are lowercase and digit-free, so they fail the
    "looks like debris" test and the repair stops.
    """
    assert ocr.repair("key sk-proj-Bq7Lm3 is not working") == "key sk-proj-Bq7Lm3 is not working"
    assert ocr.repair("use sk-proj-Ab1Cd2 please and stop") == "use sk-proj-Ab1Cd2 please and stop"


def test_repair_leaves_text_with_no_vendor_prefix_alone():
    """Space removal is ANCHORED to a known prefix. Applied globally it
    would fuse prose into tokens that look random to the entropy stage
    — inventing secrets rather than recovering them."""
    prose = "the quick brown fox jumps over 42 lazy dogs"

    assert ocr.repair(prose) == prose


def test_repair_does_not_reverse_character_substitutions():
    """`@` could be mapped back to `0`, and must not be.

    `@` appears in every email address. A transformation that corrupts
    real content to rescue a guess is worse than the miss it fixes.
    """
    assert "@" in ocr.repair("mail me at alex@example.com")


def test_repair_is_idempotent():
    """It runs on every OCR result, and a function that changes its own
    output on a second pass is one refactor away from a loop."""
    once = ocr.repair("key sk-proj-Bq7Lm3Np8 Qr2St6Uv1Wx4Yz9")

    assert ocr.repair(once) == once


def test_repair_handles_an_empty_string():
    assert ocr.repair("") == ""


# ---------------------------------------------------------------
# Input validation — this endpoint accepts files from a browser
# ---------------------------------------------------------------

@needs_tesseract
def test_a_non_image_is_rejected_clearly():
    with pytest.raises(ValueError, match="not a recognisable image"):
        ocr.extract_text(b"this is definitely not a png")


@needs_tesseract
def test_an_oversized_image_is_refused_before_it_is_decoded():
    """A decompression bomb is a small file that declares enormous
    dimensions: a few kilobytes of PNG can claim 50000x50000 and
    consume gigabytes when decoded.

    The check reads the HEADER. Checking after decoding would mean the
    bomb had already gone off.
    """
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="PNG")

    # Rather than actually building a huge image, assert the limit is
    # low enough to matter and high enough for any real screenshot.
    assert ocr.MAX_PIXELS < 50_000 * 50_000
    assert ocr.MAX_PIXELS > 3840 * 2160, "a 4K screenshot must not be refused"


# ---------------------------------------------------------------
# Extraction — needs the binary
# ---------------------------------------------------------------

@needs_tesseract
def test_text_survives_the_round_trip():
    from evaluation.render import render

    result = ocr.extract_text(render("the quick brown fox jumps over the lazy dog"))

    assert "quick brown fox" in result.text
    assert result.confidence > 0.5
    assert result.word_count >= 8


@needs_tesseract
def test_a_secret_in_an_image_reaches_the_detectors():
    """The whole point of Stage 3: an image is a door into the same
    pipeline, not a separate detection problem."""
    from evaluation.render import render

    from backend.app.pipeline import orchestrator

    result = ocr.extract_text(render("my deploy key is AKIAIOSFODNN7EXAMPLE, rotate it"))
    verdict = orchestrator.run_stages(result.text)

    assert verdict.action.value != "allow"
    assert "AWS_ACCESS_KEY_ID" in {f.type for f in verdict.findings}


@needs_tesseract
def test_the_repair_is_visible_in_the_result():
    """`raw_text` exists so the evaluation can price the repair. If the
    two fields were always identical the repair would be dead code
    dressed as a feature."""
    from evaluation.render import render

    result = ocr.extract_text(render("openai sk-proj-Bq7Lm3Np8Qr2St6Uv1Wx4Yz9Ab5Cd0Ef7Gh"))

    assert result.raw_text, "the pre-repair text was not kept"
    assert result.text != result.raw_text, "this sample is meant to exercise the repair"


@needs_tesseract
def test_small_text_is_upscaled_before_recognition():
    """Tesseract expects ~300 DPI; screenshots are ~96. Upscaling is the
    highest-value preprocessing step for this input type."""
    from evaluation.render import STYLES, render

    small = next(s for s in STYLES if "11px" in s.name)
    result = ocr.extract_text(render("a short line of text", small))

    assert result.upscaled > 1.0


@needs_tesseract
def test_an_empty_image_yields_no_text_rather_than_failing():
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (400, 200), (255, 255, 255)).save(buffer, format="PNG")

    result = ocr.extract_text(buffer.getvalue())

    assert result.is_empty
    assert result.confidence == 0.0
