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

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import analyze as analyze_routes
from .db import session
from .detectors import semantic_detector

# One log line per request, with the decision but never the content.
# Configured here rather than per-module so every logger in the app
# inherits the same format and a request can be followed across files.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Bumped by hand as the API's behaviour changes. Deliberately separate
# from the extension's version: they are two independently deployable
# components, and from Week 3 the extension will send its own version
# in each request so mismatches are visible in the logs rather than
# showing up as mysterious failures.
API_VERSION = "0.1.0"

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup and shutdown hooks.

    The modern replacement for @app.on_event, which FastAPI has
    deprecated. Code before `yield` runs at startup, code after it at
    shutdown, and having both in one function makes it obvious when a
    resource is acquired but never released.

    Warming up the Stage 2 model here moves the ~1s spaCy load to a
    moment when nobody is waiting. Without it, exactly one unlucky user
    per server restart pays that cost on top of their analysis and
    experiences ShadowGuard as broken.

    Deliberately tolerant of failure: a missing model should degrade
    Stage 2, not stop the process serving Stage 1. A DLP tool that
    refuses to start because one detector is unavailable protects
    nothing at all.
    """
    log = logging.getLogger(__name__)

    try:
        semantic_detector.warm_up()
    except Exception:  # noqa: BLE001 - startup must never be fatal
        log.exception("Stage 2 model failed to load; semantic detection will be unavailable")

    # Creating the schema here rather than at import keeps `import
    # backend.app` free of filesystem side effects — the evaluation
    # harness imports the pipeline hundreds of times and must never
    # touch a database.
    #
    # Tolerant of failure for the same reason as the model load: a tool
    # that refuses to start because it cannot write its audit log
    # protects nothing at all. Detection works without persistence; the
    # dashboard is simply empty.
    try:
        session.create_tables()
        log.info("decision store ready at %s", session.database_url())
    except Exception:  # noqa: BLE001
        log.exception("could not open the decision store; decisions will not be persisted")

    yield


app = FastAPI(
    lifespan=lifespan,
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


# ---------------------------------------------------------------
# CORS
# ---------------------------------------------------------------
# Worth being precise about, because it is easy to cargo-cult.
#
# The extension's SERVICE WORKER does not actually need this. It runs
# with the extension's own origin and Chrome grants it access to
# whatever the manifest lists in host_permissions, so its requests are
# not subject to the usual cross-origin refusal. That is precisely why
# the architecture puts every network call in the worker.
#
# This middleware is here for the other two callers:
#   - the admin dashboard (Week 7), which IS an ordinary web page and
#     will be refused without it
#   - a content script, if a future change ever tries to call directly
#     — better a working request than an hour spent on an opaque
#     browser error
#
# allow_origin_regex rather than a literal list because an unpacked
# extension's id changes if it is loaded from a different path, and
# hardcoding one id produces a failure that looks like a network
# problem. Acceptable for a local prototype; a deployed build would
# pin the exact published id, and that belongs in the report's
# limitations.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    # No cookies or auth headers cross this boundary, so credentials
    # stay off. Enabling them would also forbid the wildcard-ish origin
    # matching above.
    allow_credentials=False,
)


# Mounted last so the app object is fully configured first. The router
# carries its own /api/v1 prefix, so the version segment lives in one
# place and cannot drift between endpoints.
app.include_router(analyze_routes.router)
