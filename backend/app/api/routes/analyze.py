"""
HTTP surface for the analysis endpoint.
================================================================

This module is deliberately thin. Its entire job is: accept a
validated request, hand it to the pipeline, log the outcome, return
the verdict. Any detection logic that appears here is in the wrong
file — see app/pipeline/orchestrator.py for why that boundary is a
practical constraint and not a style preference.

An APIRouter is FastAPI's unit of route grouping. Declaring the prefix
and tags once here means every endpoint in the file inherits them, so
the version segment cannot drift between routes — and adding /api/v2
later is a new router, not a search-and-replace.
"""

import logging

from fastapi import APIRouter

from ...pipeline import orchestrator
from ...schemas.analyze import AnalyzeRequest, AnalyzeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """Inspect a message the user is about to send, and rule on it.

    The extension has already cancelled the send and is holding the
    message, so this call sits directly in the user's path: every
    millisecond here is a millisecond they spend staring at a composer
    that has not responded. That is why latency is measured and
    reported rather than assumed.

    `def` rather than `async def` is intentional. The detection stages
    are CPU-bound, and FastAPI runs synchronous handlers in a thread
    pool — whereas an `async def` that never awaits would block the
    event loop for its entire duration and stall every other request.
    """
    result = orchestrator.analyze(request)

    # Log the DECISION, never the content.
    #
    # This is the single most important privacy rule in the codebase.
    # Logging prompts would recreate, in a plaintext file, exactly the
    # exposure the project exists to prevent — and server logs are
    # copied, shipped and retained far more casually than databases.
    # Length and entity types are enough to debug with.
    logger.info(
        "analyze id=%s user=%s platform=%s trigger=%s chars=%d "
        "action=%s score=%d entities=%s latency=%.2fms",
        result.request_id,
        request.user_id,
        request.platform,
        request.trigger,
        len(request.text),
        result.action.value,
        result.score,
        [e.type for e in result.entities],
        result.latency_ms,
    )

    return result
