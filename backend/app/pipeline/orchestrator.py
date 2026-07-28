"""
Detection pipeline orchestrator.
================================================================

Owns the sequencing of the detection stages and the fusion of their
results into one verdict. Route handlers call `analyze()`; so does the
evaluation harness, directly, with no HTTP involved.

That second caller is the reason this module exists as a separate
layer rather than living inside the route. The Week 3 harness runs the
whole pipeline over a labelled corpus a few hundred times to produce
precision, recall and false-positive rates. Doing that through HTTP
would mean running a server, paying network overhead per sample, and
parsing JSON back into objects — slow enough that you stop rerunning
it, and the numbers go stale.

Today the body is a stub. Tasks 3.4 onward replace the marked section
with real detectors; nothing above or below it needs to change, and
neither does the extension.
"""

import time
import uuid

from ..schemas.analyze import (
    Action,
    AnalyzeRequest,
    AnalyzeResponse,
    DetectedEntity,
    RiskLevel,
)


def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """Run the detection pipeline over one submission.

    Deliberately synchronous. The detectors are CPU-bound — regex,
    entropy, spaCy inference — so `async def` would buy nothing and
    would actively mislead: an async function that never awaits blocks
    the event loop for its whole duration, which is worse than being
    honest about it. FastAPI runs plain `def` handlers in a thread
    pool, which is the correct treatment for CPU-bound work.

    :param request: validated payload from the extension
    :returns: the verdict, including timing and which stages ran
    """
    started = time.perf_counter()

    # A correlation id generated per request and threaded through the
    # log line, the response and (from Week 5) the database row. When
    # a user reports "it blocked something it shouldn't have", this is
    # what turns three separate systems into one traceable story.
    request_id = uuid.uuid4().hex[:12]

    stages_run: list[str] = []
    entities: list[DetectedEntity] = []

    # =============================================================
    # STUB — replaced in Task 3.4 by the real Stage 1 detectors.
    #
    # Two substring checks, in Python rather than JavaScript, so that
    # the shape of the pipeline can be tested end to end before any
    # detection exists. It lives here rather than in the route so the
    # swap in 3.4 touches exactly one function.
    #
    # It is not a detector and must not grow into one: no regex
    # library, no scoring model, no config. Task 3.4 deletes it.
    # =============================================================
    text = request.text
    stages_run.append("stub")

    if "sk-" in text or "BEGIN PRIVATE KEY" in text:
        action, level, score = Action.BLOCK, RiskLevel.CRITICAL, 95
        message = "A credential was detected in this message. Sending has been blocked."
        entities.append(
            DetectedEntity(
                type="STUB_SECRET",
                start=max(text.find("sk-"), 0),
                end=max(text.find("sk-"), 0) + 3,
                score=1.0,
                detector="stub",
            )
        )
    elif "password" in text.lower():
        action, level, score = Action.WARN, RiskLevel.HIGH, 70
        message = (
            "This message appears to contain credential-like content. "
            "Review it before sending."
        )
        entities.append(
            DetectedEntity(
                type="STUB_CREDENTIAL",
                start=max(text.lower().find("password"), 0),
                end=max(text.lower().find("password"), 0) + 8,
                score=0.8,
                detector="stub",
            )
        )
    else:
        action, level, score = Action.ALLOW, RiskLevel.LOW, 0
        message = "No sensitive content detected."
    # ============================ end stub =======================

    # Measured around the detection work only, excluding FastAPI's
    # request parsing and response serialisation. That keeps the number
    # comparable across the regex-only baseline and the full pipeline —
    # which is precisely the comparison the report has to make.
    latency_ms = (time.perf_counter() - started) * 1000

    return AnalyzeResponse(
        request_id=request_id,
        action=action,
        level=level,
        score=score,
        entities=entities,
        message=message,
        redacted_text=None,
        latency_ms=round(latency_ms, 3),
        stages_run=stages_run,
    )
