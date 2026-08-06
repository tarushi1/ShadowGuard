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

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ...db import events as event_store
from ...pipeline import orchestrator
from ...schemas.analyze import MAX_IMAGE_BYTES, AnalyzeRequest, AnalyzeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, background: BackgroundTasks) -> AnalyzeResponse:
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

    # ---- Persist AFTER the response has been sent ----------------
    # A BackgroundTask runs once the response is on its way, so the
    # database write is not in the user's path. That matters here more
    # than it usually would: the extension is holding a cancelled send
    # and the person is staring at a composer that has not responded,
    # so every millisecond spent here is one they experience.
    #
    # The write is passed METADATA and the response — never the request
    # and never the text. `char_count` is computed here so that
    # `record_decision` never has to accept the content at all.
    background.add_task(
        event_store.record_decision,
        result,
        user_id=request.user_id,
        platform=request.platform,
        trigger=request.trigger,
        client_version=request.client_version,
        char_count=len(request.text),
    )

    return result


@router.post("/analyze/image", response_model=AnalyzeResponse)
async def analyze_image(
    background: BackgroundTasks,
    file: UploadFile = File(description="Screenshot or pasted image to inspect."),
    user_id: str = Form(...),
    platform: str = Form("chatgpt"),
    trigger: str | None = Form(None),
    client_version: str | None = Form(None),
) -> AnalyzeResponse:
    """Inspect an image the user is about to send.

    A separate endpoint rather than a field on `/analyze`, because
    images and text differ in size, validation and failure modes. A
    schema whose text field caps at 100k characters while a sibling
    routinely carries several megabytes is one that has stopped
    describing anything.

    `async def` here, unlike the text endpoint. Reading an upload is
    genuinely I/O-bound and `await file.read()` releases the event loop
    while the browser streams; the CPU-bound OCR that follows is then
    handed to a worker thread explicitly. Marking this `def` would
    block the loop for the whole upload.

    Nothing is written to disk at any point. The bytes go from the
    request into memory, through Tesseract, and out of scope when this
    returns — only entity types and measurements survive (D41).
    """
    payload = await file.read()

    # Checked AFTER reading, which is a real limitation and is stated
    # rather than hidden: FastAPI has already buffered the upload by
    # this point, so an oversized file has cost memory before it is
    # refused. Enforcing it earlier means a middleware reading
    # Content-Length, which a client controls and can lie about. For a
    # localhost prototype the buffered cost is acceptable; a deployed
    # version would cap it at the reverse proxy, where the limit cannot
    # be argued with.
    if len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)}MB",
        )

    if not payload:
        raise HTTPException(status_code=422, detail="empty upload")

    try:
        # Off the event loop. OCR is CPU-bound and takes ~200ms on a
        # screenshot; running it inline would stall every other request
        # for that whole time.
        result, extracted = await run_in_threadpool(orchestrator.analyze_image, payload)
    except RuntimeError as error:
        # Tesseract missing. A 503 rather than a 500: the service is
        # fine, one capability is unavailable, and the distinction is
        # what tells an operator to install a package rather than to
        # read a stack trace.
        logger.error("OCR unavailable: %s", error)
        raise HTTPException(status_code=503, detail="OCR is not available") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # Logs the SHAPE of what was read, never a word of it.
    logger.info(
        "analyze-image id=%s user=%s platform=%s bytes=%d %dx%d "
        "ocr_words=%d ocr_conf=%.2f action=%s score=%d entities=%s latency=%.2fms",
        result.request_id,
        user_id,
        platform,
        len(payload),
        extracted.width,
        extracted.height,
        extracted.word_count,
        extracted.confidence,
        result.action.value,
        result.score,
        [e.type for e in result.entities],
        result.latency_ms,
    )

    background.add_task(
        event_store.record_decision,
        result,
        user_id=user_id,
        platform=platform,
        trigger=trigger,
        client_version=client_version,
        char_count=len(extracted.text),
        source="image",
    )

    return result
