"""
Request and response contract for the analysis endpoint.
================================================================

These models ARE the API specification. FastAPI derives runtime
validation, the OpenAPI document and the interactive /docs form from
them, so there is no separate spec to drift out of sync with the code.

Two decisions here matter more than the rest, and both are security
decisions rather than design preferences:

  1. The response never echoes the user's text back. It returns
     OFFSETS into text the client already has. A DLP service that
     replies with the secret it just found has doubled the number of
     places that secret exists, and put a copy in every HTTP log,
     proxy cache and error report between here and the browser.

  2. `text` has a hard length limit. Without one, a single request can
     pin the CPU inside a regex or spaCy for minutes. In a local
     prototype that is an annoyance; the same endpoint reachable on a
     network is a denial-of-service hole.
"""

from enum import Enum

from pydantic import BaseModel, Field

# A prompt long enough to cover any realistic paste, short enough that
# the detection stages stay bounded. 100_000 characters is roughly a
# 30-page document.
MAX_TEXT_LENGTH = 100_000

# Images arrive on their own endpoint as a multipart upload, NOT as
# base64 inside this schema — the placeholder that used to sit here was
# removed in Week 6 rather than left as an unused field in a published
# contract.
#
# Base64 would have kept one endpoint at the cost of a 33% size
# increase and a schema in which one field caps at 100k characters
# while another routinely carries several megabytes. Images and text
# differ in size, validation and failure modes; one endpoint pretending
# otherwise makes both harder to reason about.
#
# 8 MB comfortably covers a 4K screenshot as PNG and refuses anything
# that is really a file transfer.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Larger than an image, and for a reason worth stating rather than
# assuming. A screenshot is one screen; a PDF is a document, and a
# perfectly ordinary scanned contract or exported report runs past 8 MB
# without being an attack. The cap that actually protects the backend
# here is not the byte count anyway — it is the page and character
# limits in `detectors/pdf.py`, because a 20 KB PDF can decompress into
# gigabytes of text while a 20 MB one is often just a photograph.
MAX_PDF_BYTES = 20 * 1024 * 1024


class Action(str, Enum):
    """What the extension should do with this message.

    Inheriting from `str` as well as `Enum` makes these serialise as
    plain strings ("allow") rather than objects, which is what the
    JavaScript side expects.

    These four values MUST stay identical to `SG.ACTION` in
    extension/src/shared/messages.js. They travel Python -> service
    worker -> content script -> UI, and a mismatch fails silently at
    whichever layer stops recognising the string.
    """

    ALLOW = "allow"
    WARN = "warn"
    REDACT = "redact"
    BLOCK = "block"


class RiskLevel(str, Enum):
    """Severity band, used for colour and wording in the modal.

    Kept separate from Action on purpose. Severity is a property of
    what was FOUND; action is a property of POLICY. An organisation
    might choose to warn rather than block on `critical`, and that
    should be a config change, not a change to the detection code.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DetectedEntity(BaseModel):
    """One piece of sensitive content found in the input.

    Offsets, not values. The client already has the text and can
    highlight `text[start:end]` itself; sending the matched substring
    back would put the secret in another log line for no benefit.
    """

    type: str = Field(
        description="Entity type, e.g. OPENAI_API_KEY, CREDIT_CARD, PERSON.",
        examples=["OPENAI_API_KEY"],
    )
    start: int = Field(ge=0, description="Start offset in the submitted text.")
    end: int = Field(ge=0, description="End offset, exclusive.")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Detector confidence, 0-1. Regex hits are near 1.0; "
        "semantic matches carry genuine uncertainty.",
    )
    detector: str = Field(
        description="Which stage found it: regex, entropy, presidio, ocr.",
        examples=["regex"],
    )


class AnalyzeRequest(BaseModel):
    """What the extension sends before a message is allowed through."""

    text: str = Field(
        max_length=MAX_TEXT_LENGTH,
        description="The prompt the user is about to send.",
    )

    user_id: str = Field(
        max_length=128,
        description=(
            "Who is sending this. NOTE: client-supplied and therefore not "
            "trustworthy — anyone can put any value here. Acceptable for a "
            "local prototype; a real deployment would take identity from an "
            "authenticated session, not the request body. Flagged in the "
            "report's limitations."
        ),
        examples=["arushi@example.com"],
    )

    platform: str = Field(
        max_length=64,
        description="Which AI platform: chatgpt, gemini.",
        examples=["chatgpt"],
    )

    trigger: str | None = Field(
        default=None,
        max_length=64,
        description="How the send was attempted: enter-key, send-button, form-submit.",
    )

    conversation_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Opaque key for the chat this message belongs to, used to carry "
            "context words between messages (D56). Set by the SERVICE WORKER "
            "from Chrome's tab id, never by the page: a conversation key the "
            "content script could choose would let a hostile page read another "
            "conversation's priming state by guessing its key. No chat URL or "
            "title is ever sent."
        ),
    )

    client_version: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "Extension version. Logged so a version mismatch shows up as a "
            "line in the logs rather than as mysterious behaviour."
        ),
    )


class OcrInfo(BaseModel):
    """What Stage 3 read, described without quoting it.

    Every field here is a MEASUREMENT of the extracted text rather than
    the text itself — the same rule the decision store follows (D41).
    `characters` tells you how much was read; nothing tells you what.

    Returned so the modal can say "we read 214 characters from this
    image at 89% confidence", which is the difference between a warning
    a user can evaluate and one they have to take on faith.
    """

    characters: int = Field(description="How much text was read. Not the text.")
    words: int
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Mean per-word OCR confidence. REPORTED, never acted on: a low "
            "value means treat a non-detection here with suspicion, and using "
            "it to skip analysis would turn a blurry screenshot into a bypass."
        ),
    )
    width: int
    height: int


class PdfInfo(BaseModel):
    """What Stage 3b read out of a document, described without quoting it.

    Same rule as `OcrInfo`: every field is a MEASUREMENT of the
    extracted text, never the text. `characters` says how much was
    read; nothing here says what.

    `has_text_layer` is the field that carries weight. False means the
    document was a scan and this stage found nothing to read — an
    absence of evidence, not evidence of absence. It is surfaced to the
    client so the modal can say "this looks like a scanned document and
    was not inspected" rather than showing the silence as a clean bill
    of health.
    """

    characters: int = Field(description="How much text was read. Not the text.")
    pages: int
    pages_read: int
    has_text_layer: bool
    truncated: bool = False
    encrypted: bool = False
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Which channels yielded text: pages, metadata, annotations, forms. "
            "Kept because it is the only way to answer 'is reading annotations "
            "worth the code?' with data rather than intuition."
        ),
    )


class AnalyzeResponse(BaseModel):
    """The verdict. The extension acts on `action` and shows the rest."""

    request_id: str = Field(
        description=(
            "Correlation id. The same value appears in the server log, the "
            "event row in SQLite and the extension console, so one incident "
            "can be traced across all three."
        )
    )

    action: Action
    level: RiskLevel

    score: int = Field(ge=0, le=100, description="Fused risk score.")

    entities: list[DetectedEntity] = Field(
        default_factory=list,
        description="What was found, as offsets. Never the matched text itself.",
    )

    message: str = Field(description="Human-readable explanation shown in the modal.")

    redacted_text: str | None = Field(
        default=None,
        description=(
            "Only populated for action=redact (Week 5). This is the one case "
            "where the server does return text, because it is returning a "
            "SAFER version than the client already holds."
        ),
    )

    latency_ms: float = Field(
        ge=0.0,
        description=(
            "Server-side processing time. Present from day one because "
            "latency is a graded metric, and a number that is not collected "
            "from the start cannot be reported on later."
        ),
    )

    stages_run: list[str] = Field(
        default_factory=list,
        description=(
            "Which detection stages actually executed. Makes short-circuiting "
            "visible: when Stage 1 finds a critical secret the pipeline skips "
            "Stage 2, and this field is the evidence."
        ),
    )

    ocr: OcrInfo | None = Field(
        default=None,
        description="Present only when the input was an image (Stage 3).",
    )

    pdf: PdfInfo | None = Field(
        default=None,
        description="Present only when the input was a PDF (Stage 3b).",
    )

    reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Why the risk engine reached this score, in plain language. "
            "A score nobody can explain is a score nobody will trust: when a "
            "user asks why their message was stopped, 'score 61' is not an "
            "answer and 'a name together with a way to contact them' is. "
            "Names entity TYPES only and never matched values, for the same "
            "reason `entities` carries offsets rather than text."
        ),
    )
