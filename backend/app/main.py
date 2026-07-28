"""
ShadowGuard AI — FastAPI application entry point.
================================================================

This module does exactly one thing: build and configure the app
object that Uvicorn serves. It deliberately contains NO detection
logic and NO endpoint bodies.

That separation is not tidiness for its own sake. The Week 3
evaluation harness imports the detectors directly and runs them over
a labelled corpus — no HTTP, no server process. If detection logic
lived in a route handler, the harness could only reach it by spinning
up a web server and issuing requests, which is slow, flaky, and makes
the precision/recall numbers painful to regenerate. Anything the
harness cannot call cheaply will not get measured, and anything that
does not get measured does not appear in the report.

Layering:

    main.py         builds the app, mounts routers, sets middleware
    api/routes/     HTTP only: parse request, call pipeline, respond
    pipeline/       orchestrates the detection stages
    detectors/      pure functions, no HTTP, no I/O, fully testable
    core/           risk fusion and the single config file

Run it with:

    .venv/bin/uvicorn backend.app.main:app --reload

  backend.app.main   the import path to this module
  :app               the variable below that Uvicorn should serve
  --reload           restart automatically when a source file changes
                     (development only — it spawns a file watcher and
                     is not something you would run in production)
"""

from fastapi import FastAPI

# Bumped by hand as the API's behaviour changes. Deliberately separate
# from the extension's version: they are two independently deployable
# components, and from Week 3 the extension will send its own version
# in each request so mismatches are visible in the logs rather than
# showing up as mysterious failures.
API_VERSION = "0.1.0"

app = FastAPI(
    title="ShadowGuard AI",
    description=(
        "Context-aware, multimodal data loss prevention for enterprise "
        "interactions with generative AI platforms."
    ),
    version=API_VERSION,
    # FastAPI serves interactive documentation generated from the type
    # hints on each endpoint. /docs is Swagger UI, where you can call
    # endpoints from the browser; /redoc is a cleaner read-only render.
    # Both are worth a screenshot in the report — they are free
    # evidence of a properly specified API contract.
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Liveness probe.

    Deliberately trivial and dependency-free: it must answer even when
    the model files are missing or the database is unreachable. Its job
    is to distinguish "the process is not running" from "the process is
    running but something inside it is broken" — collapsing those two
    into one signal is how you spend an hour debugging the wrong layer.

    The extension will call this on startup to decide whether to show
    a "backend unavailable" state rather than silently failing open.
    """
    return {"status": "ok", "service": "shadowguard-ai", "version": API_VERSION}
