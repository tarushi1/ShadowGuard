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

    image_b64: str | None = Field(
        default=None,
        description="Base64 image for OCR. Unused until Week 6.",
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

    client_version: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "Extension version. Logged so a version mismatch shows up as a "
            "line in the logs rather than as mysterious behaviour."
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
