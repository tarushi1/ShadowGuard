"""
The administrative dashboard.
================================================================

Server-rendered from the same FastAPI application that serves the
analysis endpoint. Not a separate React app, and not Flask, for three
reasons that are worth stating because "we used React" is the default
answer to this question:

  - NO SECOND PROCESS. A demo that needs two servers started in the
    right order is a demo that breaks. This mounts on the backend that
    is already running.
  - THE QUERIES ALREADY EXIST. `db/events.py` was written in Week 5
    alongside the schema, specifically so the indexes declared there
    would have real readers rather than being added speculatively.
  - NO EXTERNAL REQUESTS. Charts are plain HTML and CSS with no chart
    library. Pulling one from a CDN would break the property this whole
    project rests on: nothing leaves the machine. A DLP tool whose
    dashboard phones out is self-defeating.

The page can display no message content because the store holds none
(D41). That is not a restraint exercised here; it is a property
inherited from the schema.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...db import events as event_store

router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))

# Longest bars are the most readable, so every chart is scaled to its
# own maximum rather than to 100. A `or 1` guards the empty database:
# dividing by zero on a fresh install would 500 the page that exists to
# tell you the install is fresh.
def _scaled(counts: dict[str, int], limit: int | None = None) -> tuple[list, int]:
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if limit:
        ranked = ranked[:limit]
    return ranked, max([c for _, c in ranked], default=0) or 1


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Everything the decision store knows, on one page.

    Deliberately read-only. A dashboard that can change policy is a
    second place where policy lives, and `core/policy.py` is supposed
    to be the only one.
    """
    actions, max_action = _scaled(event_store.action_counts())
    detectors, max_detector = _scaled(event_store.detector_counts())
    entities, max_entity = _scaled(event_store.entity_counts(), limit=10)

    histogram = event_store.score_histogram()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": event_store.summary(),
            "actions": actions,
            "max_action": max_action,
            "detectors": detectors,
            "max_detector": max_detector,
            "entities": entities,
            "max_entity": max_entity,
            "histogram": histogram,
            "max_bucket": max((c for _, c in histogram), default=0),
            "recent": event_store.recent(limit=25),
        },
    )
