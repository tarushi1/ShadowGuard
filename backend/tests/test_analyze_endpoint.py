"""
Tests for POST /api/v1/analyze.
================================================================

Run with:   .venv/bin/pytest backend/tests -v

FastAPI's TestClient drives the real application in-process — routing,
validation and serialisation all execute exactly as they would over
the network, but with no server, no port and no network stack. Tests
run in milliseconds, which is what makes it realistic to run them on
every change rather than "before submitting".

These tests assert the CONTRACT, not the stub's opinions. The stub is
deleted in Task 3.4; if the tests encoded which strings it happens to
flag, they would all break on the day the real detectors land, and
would have told us nothing in the meantime.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/analyze"


def payload(text: str, **overrides) -> dict:
    """Minimal valid request body, so each test states only what it varies."""
    body = {
        "text": text,
        "user_id": "test-user",
        "platform": "chatgpt",
        "trigger": "enter-key",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------
# Contract
# ---------------------------------------------------------------

def test_returns_200_and_every_declared_field():
    response = client.post(ENDPOINT, json=payload("hello world"))

    assert response.status_code == 200
    body = response.json()

    for field in (
        "request_id", "action", "level", "score",
        "entities", "message", "latency_ms", "stages_run",
    ):
        assert field in body, f"response is missing {field!r}"


def test_action_and_level_are_within_the_allowed_vocabularies():
    """These strings cross into JavaScript, where a typo fails silently.

    The extension routes on `action` by string comparison. A value the
    content script does not recognise falls through every branch and
    the message is neither sent nor blocked — the worst outcome, and
    one that raises no error anywhere.
    """
    body = client.post(ENDPOINT, json=payload("hello")).json()

    assert body["action"] in {"allow", "warn", "redact", "block"}
    assert body["level"] in {"low", "medium", "high", "critical"}


def test_score_stays_inside_the_declared_range():
    body = client.post(ENDPOINT, json=payload("hello")).json()
    assert 0 <= body["score"] <= 100


def test_request_ids_are_unique_per_request():
    """Two requests must be distinguishable in the logs and the database."""
    a = client.post(ENDPOINT, json=payload("one")).json()["request_id"]
    b = client.post(ENDPOINT, json=payload("two")).json()["request_id"]
    assert a != b


def test_a_critical_regex_hit_short_circuits_later_stages():
    """The optimisation the report claims, asserted rather than asserted-to.

    A critical pattern match is already certain and already blocking,
    so no later stage can change the verdict. Running them would buy
    nothing and cost real latency — Stage 2's spaCy inference alone is
    roughly 50ms against ~0.04ms for everything currently in the
    pipeline.

    `stages_run` is what makes this evidence instead of a claim.
    """
    body = client.post(ENDPOINT, json=payload("deploy key AKIAIOSFODNN7EXAMPLE")).json()

    assert body["action"] == "block"
    assert body["stages_run"] == ["regex"], "entropy should have been skipped"


def test_non_critical_input_runs_every_stage():
    """The other half of the same claim: short-circuiting must be
    conditional, not an excuse for never running the later stages."""
    body = client.post(ENDPOINT, json=payload("how do I sort a list")).json()

    assert body["stages_run"] == ["regex", "entropy"]


def test_latency_is_measured_and_plausible():
    """Latency is a graded metric, so it has to be real, not a placeholder."""
    body = client.post(ENDPOINT, json=payload("hello")).json()
    assert body["latency_ms"] > 0
    assert body["latency_ms"] < 1000


# ---------------------------------------------------------------
# The privacy property
# ---------------------------------------------------------------

def test_response_never_echoes_the_submitted_text():
    """The single most important assertion in this file.

    A DLP service that returns the secret it just found has doubled the
    number of places that secret exists and put a copy in every HTTP
    log, proxy cache and error report on the way back to the browser.
    Entities carry OFFSETS; the client already has the text.

    This test is what stops a future refactor from quietly adding an
    `input_text` field for debugging convenience.
    """
    secret = "sk-proj-zzzTOPSECRET999"
    body = client.post(ENDPOINT, json=payload(f"my key is {secret}")).json()

    assert secret not in response_text(body)


def response_text(body: dict) -> str:
    """Flatten the response to a string so we can search all of it."""
    import json

    return json.dumps(body)


# ---------------------------------------------------------------
# Validation — behaviour we get from Pydantic, and must not lose
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "body, missing",
    [
        ({"text": "hi", "platform": "chatgpt"}, "user_id"),
        ({"text": "hi", "user_id": "u"}, "platform"),
        ({"user_id": "u", "platform": "chatgpt"}, "text"),
    ],
)
def test_missing_required_fields_are_rejected(body, missing):
    response = client.post(ENDPOINT, json=body)

    assert response.status_code == 422
    assert missing in str(response.json())


def test_wrong_type_is_rejected():
    body = payload("placeholder")
    body["text"] = 123  # not a string

    assert client.post(ENDPOINT, json=body).status_code == 422


def test_oversized_text_is_rejected():
    """Without a length cap, one request can pin the CPU inside a regex
    or spaCy for minutes. Locally that is an annoyance; the same
    endpoint reachable on a network is a denial-of-service hole.
    """
    from backend.app.schemas.analyze import MAX_TEXT_LENGTH

    response = client.post(ENDPOINT, json=payload("a" * (MAX_TEXT_LENGTH + 1)))
    assert response.status_code == 422


def test_optional_fields_may_be_omitted():
    body = {"text": "hi", "user_id": "u", "platform": "chatgpt"}
    assert client.post(ENDPOINT, json=body).status_code == 200


# ---------------------------------------------------------------
# Logging
# ---------------------------------------------------------------

def test_logs_the_decision_but_never_the_content(caplog):
    """Server logs are copied, shipped and retained far more casually
    than databases. Logging prompts would recreate, in a plaintext
    file, exactly the exposure this project exists to prevent.
    """
    secret = "sk-proj-zzzTOPSECRET999"

    with caplog.at_level("INFO"):
        client.post(ENDPOINT, json=payload(f"my key is {secret}"))

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert "analyze id=" in logged, "the request was not logged at all"
    assert secret not in logged, "the prompt leaked into the logs"


# ---------------------------------------------------------------
# System
# ---------------------------------------------------------------

def test_health_has_no_dependencies():
    """It must answer even when models are missing or the database is
    unreachable, so 'the process is down' stays distinguishable from
    'the process is up but broken'.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
