"""
Stage 3 — optical character recognition.
================================================================

WHAT THIS IS NOT
----------------
Not a detector. Every other module in this package answers "is this
sensitive?"; this one answers "what does this image say?".

The distinction shapes the whole design. OCR produces TEXT, and that
text then goes through Stages 1 and 2 completely unchanged — the same
regex rules, the same entropy thresholds, the same Presidio engine, the
same risk policy. A screenshot of an API key is not a new detection
problem; it is the old one behind a new door.

That is why this file contains no rules and no scoring. It is a text
SOURCE, and the pipeline it feeds already exists.

THE LIMITATION THAT MATTERS MOST
--------------------------------
OCR errors break exact-match detection, and they break it SILENTLY.

Stage 1's precision comes from patterns that match exactly:
`AKIA[0-9A-Z]{16}` either matches or it does not. OCR routinely
confuses characters that look alike:

    0 <-> O      1 <-> l <-> I      5 <-> S      8 <-> B      2 <-> Z

One such substitution anywhere in a twenty-character key means the
regex does not fire, the entropy stage may still catch it as
"random-looking", and a checksum-validated rule like the credit-card
one fails outright — a single wrong digit fails Luhn.

So the honest expectation is that **detection through OCR is worse than
detection on typed text**, and the interesting number is HOW MUCH
worse. That is measured in `evaluation/ocr_eval.py` against images
rendered from known text, rather than asserted here.

PRIVACY
-------
The image is never written to disk. It arrives as bytes, is decoded in
memory, and both the pixels and the extracted text are discarded when
this function returns — only entity TYPES and counts survive into the
decision store (D41). A screenshot is more sensitive than the text
inside it: it also carries hostnames, usernames, window titles, and
whatever else happened to be on screen.
"""

import logging
import shutil
from dataclasses import dataclass
from io import BytesIO

logger = logging.getLogger(__name__)

DETECTOR_NAME = "ocr"

# Formats worth accepting. Deliberately a small allowlist rather than
# "whatever Pillow can open": Pillow supports dozens of formats, some
# with decoders that have had CVEs, and a DLP tool is an unusually
# attractive thing to feed a malformed file to.
SUPPORTED_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP", "BMP"})

# Cap on decoded pixels, checked BEFORE decoding.
#
# A "decompression bomb" is a small file that expands enormously — a
# few kilobytes of PNG can declare 50000x50000 pixels and consume
# gigabytes when decoded. Pillow warns above its own limit; this makes
# it a refusal rather than a warning.
MAX_PIXELS = 40_000_000  # ~8000x5000, comfortably above any screenshot

# Tesseract is trained largely on scanned print at ~300 DPI. Screenshots
# are typically 96 DPI, and small text in them lands well below what the
# model expects. Upscaling before recognition is the single highest-value
# preprocessing step for this input type — measured, not assumed: see
# evaluation/ocr_eval.py.
MIN_HEIGHT_FOR_OCR = 1000
MAX_UPSCALE = 3.0


@dataclass(frozen=True)
class OcrResult:
    """What an image turned out to say."""

    text: str
    """Extracted text. Passed straight to Stages 1 and 2, never stored."""

    confidence: float
    """Mean per-word confidence, 0-1, or 0.0 when nothing was found.

    Reported rather than acted on. A low value means "treat any
    non-detection here with suspicion" — it does NOT mean the image is
    safe, and using it to skip analysis would turn a blurry screenshot
    into a bypass.
    """

    width: int
    height: int
    word_count: int

    upscaled: float = 1.0
    """The factor applied before recognition, for the report's
    preprocessing discussion."""

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def available() -> bool:
    """Is the Tesseract binary installed?

    `pytesseract` is only a wrapper — installing it via pip does NOT
    install the engine, which is a system package. Without this check
    the failure surfaces as a confusing `TesseractNotFoundError` deep
    inside a request rather than as a clear startup message.
    """
    return shutil.which("tesseract") is not None


def _prepare(image):
    """Preprocessing, kept deliberately minimal.

    Two operations, both justified by what Tesseract expects:

    GRAYSCALE — recognition works on intensity, so colour is discarded
    anyway. Doing it explicitly avoids a surprise on images with an
    alpha channel, where transparent regions can decode as black and
    swallow dark text.

    UPSCALE — the important one. Tesseract expects roughly 300 DPI;
    screenshots are usually 96. LANCZOS resampling is used because
    nearest-neighbour introduces jagged edges that the recognizer reads
    as character strokes.

    NOT DONE HERE: binarisation, deskewing, denoising. They help on
    photographed documents and actively hurt on clean screenshots, which
    is the input this project actually receives. Adding transformations
    that "usually help" without measuring them on the real input is how
    preprocessing pipelines become folklore.
    """
    from PIL import Image

    prepared = image.convert("L")
    factor = 1.0

    if prepared.height < MIN_HEIGHT_FOR_OCR:
        factor = min(MAX_UPSCALE, MIN_HEIGHT_FOR_OCR / max(prepared.height, 1))
        prepared = prepared.resize(
            (int(prepared.width * factor), int(prepared.height * factor)),
            Image.LANCZOS,
        )

    return prepared, factor


def extract_text(image_bytes: bytes) -> OcrResult:
    """Read the text out of an image.

    Pure with respect to the filesystem: nothing is written, and the
    decoded image exists only for the duration of this call.

    :param image_bytes: the raw uploaded file
    :raises ValueError: on an unsupported, malformed or oversized image
    :raises RuntimeError: when the Tesseract binary is missing
    """
    if not available():
        raise RuntimeError(
            "the tesseract binary is not installed — `brew install tesseract`"
        )

    import pytesseract
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(BytesIO(image_bytes))
    except UnidentifiedImageError as error:
        raise ValueError("not a recognisable image") from error

    if image.format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported image format: {image.format}")

    # Checked from the HEADER, before any pixel is decoded. Checking
    # afterwards would mean the bomb had already gone off.
    if image.width * image.height > MAX_PIXELS:
        raise ValueError(f"image too large: {image.width}x{image.height}")

    prepared, factor = _prepare(image)

    # image_to_data rather than image_to_string, because the per-word
    # confidences are only available from the structured output, and a
    # result with no confidence attached cannot be reasoned about.
    data = pytesseract.image_to_data(
        prepared, output_type=pytesseract.Output.DICT
    )

    words: list[str] = []
    confidences: list[float] = []

    for word, confidence in zip(data["text"], data["conf"], strict=False):
        if not word.strip():
            continue

        words.append(word)

        # Tesseract reports -1 for entries it did not actually score.
        # Averaging those in would drag every result toward nonsense.
        value = float(confidence)
        if value >= 0:
            confidences.append(value / 100.0)

    return OcrResult(
        # Space-joined rather than newline-joined. Line structure is
        # lost either way at this level, and a space is the safer
        # separator: joining without one would fuse adjacent words into
        # a single token that could look like a secret to the entropy
        # stage.
        text=" ".join(words),
        confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        width=image.width,
        height=image.height,
        word_count=len(words),
        upscaled=round(factor, 2),
    )
