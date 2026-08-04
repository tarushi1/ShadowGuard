"""
Tests for the decision store.
================================================================

The first test in this file is the one that matters. Everything else
checks that persistence works; that one checks that it does not store
what it must never store, and it does so by searching the database
FILE — bytes on disk — rather than by asking the ORM politely.

That distinction is the lesson of D39: a check that reads the same
layer it wrote to will agree with itself.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from backend.app.db import events as event_store
from backend.app.db import session
from backend.app.main import app
from backend.app.models.event import FORBIDDEN_COLUMN_NAMES, Base, DecisionEvent

client = TestClient(app)
ENDPOINT = "/api/v1/analyze"


def payload(text: str, **overrides) -> dict:
    body = {"text": text, "user_id": "test-user", "platform": "chatgpt"}
    body.update(overrides)
    return body


def database_bytes() -> bytes:
    """Every byte of the database file, including WAL.

    Reading the raw file is the point. SQLite buffers recent writes in
    a write-ahead log, so a secret could sit in `test.db-wal` while
    `test.db` looks clean — and a test that checked only the main file
    would pass while the data was on disk a few hundred bytes away.
    """
    from pathlib import Path

    url = session.database_url()
    base = Path(url.removeprefix("sqlite:///"))

    data = b""
    for suffix in ("", "-wal", "-shm"):
        candidate = base.with_name(base.name + suffix)
        if candidate.exists():
            data += candidate.read_bytes()

    return data


# ---------------------------------------------------------------
# The property this whole design exists to guarantee
# ---------------------------------------------------------------

def test_no_prompt_text_ever_reaches_the_database_file():
    """The single most important test in the project.

    A DLP tool's own database is the most concentrated collection of
    sensitive prompts in an organisation if anyone lets it become one.
    "Do not store prompt text" is one convenient five-line change away
    from being false, so it is asserted against the file itself.
    """
    secret = "sk-proj-zzzTOPSECRET999abcdef"
    distinctive = "pangolin-marmalade-scaffold"

    client.post(ENDPOINT, json=payload(f"my key is {secret} and {distinctive}"))

    raw = database_bytes()

    assert raw, "no database file was written at all"
    assert secret.encode() not in raw, "a secret was written to the database"
    assert distinctive.encode() not in raw, "ordinary prompt text was written to the database"


def test_the_schema_has_no_column_that_could_hold_a_prompt():
    """Structural, not behavioural.

    The test above would still pass if someone added a `text` column
    and simply did not populate it yet — and the next person would
    populate it. This fails at the moment the column appears.
    """
    inspector = inspect(session.get_engine())

    for table in Base.metadata.tables:
        for column in inspector.get_columns(table):
            assert column["name"].lower() not in FORBIDDEN_COLUMN_NAMES, (
                f"{table}.{column['name']} could hold user content — see models/event.py"
            )


def test_reasons_are_stored_but_name_only_entity_types():
    """`reasons` is free text on the response, so it is the one field
    through which a value could plausibly escape into the store."""
    secret = "sk-proj-zzzTOPSECRET999abcdef"

    client.post(ENDPOINT, json=payload(f"my key is {secret}"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.reasons, "a stopped message recorded no explanation"
    assert secret not in event.reasons


# ---------------------------------------------------------------
# That it records the right things
# ---------------------------------------------------------------

def test_a_decision_is_recorded_with_its_verdict():
    response = client.post(ENDPOINT, json=payload("charge 4532015112830366 for the invoice")).json()

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.request_id == response["request_id"], "the correlation id does not match"
    assert event.action == response["action"]
    assert event.score == response["score"]
    assert event.user_id == "test-user"


def test_the_length_is_recorded_instead_of_the_text():
    text = "how do I sort a list in python"

    client.post(ENDPOINT, json=payload(text))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.char_count == len(text)


def test_entities_are_recorded_as_types_and_offsets():
    client.post(ENDPOINT, json=payload("my key is AKIAIOSFODNN7EXAMPLE"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()
        entities = list(event.entities)

    assert entities, "a detected entity was not recorded"
    assert entities[0].entity_type == "AWS_ACCESS_KEY_ID"
    assert entities[0].end_offset > entities[0].start_offset

    # Offsets are positions, not content — and useless without the text
    # they index into, which is exactly why they are safe to keep.
    assert not hasattr(entities[0], "value")


def test_an_allowed_message_is_recorded_too():
    """Recording only the blocks would make the store useless for the
    thing it is for: a false-positive rate needs a denominator."""
    client.post(ENDPOINT, json=payload("how do I reverse a string"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.action == "allow"


def test_stages_run_is_recorded_so_the_short_circuit_stays_measurable():
    client.post(ENDPOINT, json=payload("deploy key AKIAIOSFODNN7EXAMPLE"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert event.stages_run == "regex", "the short-circuit is not visible after the fact"


def test_request_ids_are_unique_in_the_store():
    """A duplicate would mean two decisions share one correlation id,
    and every trace through the logs becomes ambiguous."""
    from sqlalchemy.exc import IntegrityError

    client.post(ENDPOINT, json=payload("hello"))

    with session.session_scope() as db:
        existing = db.query(DecisionEvent).one()
        duplicate_id = existing.request_id

    with pytest.raises(IntegrityError):
        with session.session_scope() as db:
            db.add(
                DecisionEvent(
                    request_id=duplicate_id, user_id="u", platform="chatgpt",
                    action="allow", level="low", score=0, char_count=1,
                    latency_ms=0.1, stages_run="regex",
                )
            )


# ---------------------------------------------------------------
# Failure must not break protection
# ---------------------------------------------------------------

def test_the_endpoint_still_answers_when_the_store_is_broken(monkeypatch):
    """A DLP tool whose PROTECTION fails because its BOOKKEEPING failed
    has the priorities backwards.

    The user has already been warned or blocked correctly by the time
    the write runs. A disk-full error must not turn that into a 500 —
    which the extension would then fail OPEN on, sending the very
    message that was just correctly identified as a leak.
    """
    def explode(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(session, "session_scope", explode)

    response = client.post(ENDPOINT, json=payload("my key is AKIAIOSFODNN7EXAMPLE"))

    assert response.status_code == 200
    assert response.json()["action"] == "block"


# ---------------------------------------------------------------
# Retention
# ---------------------------------------------------------------

def test_purging_an_event_takes_its_entities_with_it():
    """Retention is a privacy control, and a cascade that does not
    cascade makes it a lie.

    SQLite has foreign keys OFF by default, per connection. Without the
    pragma in session.py, `ondelete="CASCADE"` is decorative: the event
    row goes and its entity rows remain, so the "deleted" data is still
    in the file.
    """
    from datetime import timedelta

    from backend.app.models.event import DecisionEntity, _utcnow

    client.post(ENDPOINT, json=payload("my key is AKIAIOSFODNN7EXAMPLE"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()
        event.created_at = _utcnow() - timedelta(days=400)
        assert db.query(DecisionEntity).count() > 0, "precondition: entities exist"

    removed = event_store.purge_older_than(days=90)

    assert removed == 1

    with session.session_scope() as db:
        assert db.query(DecisionEvent).count() == 0
        assert db.query(DecisionEntity).count() == 0, "entity rows were orphaned, not deleted"


def test_purging_leaves_recent_events_alone():
    client.post(ENDPOINT, json=payload("hello"))

    assert event_store.purge_older_than(days=90) == 0

    with session.session_scope() as db:
        assert db.query(DecisionEvent).count() == 1


# ---------------------------------------------------------------
# Queries the dashboard will use
# ---------------------------------------------------------------

def test_action_counts_group_by_outcome():
    client.post(ENDPOINT, json=payload("how do I sort a list"))
    client.post(ENDPOINT, json=payload("my key is AKIAIOSFODNN7EXAMPLE"))

    counts = event_store.action_counts()

    assert counts.get("allow") == 1
    assert counts.get("block") == 1


def test_entity_counts_show_which_detectors_actually_fire():
    client.post(ENDPOINT, json=payload("my key is AKIAIOSFODNN7EXAMPLE"))
    client.post(ENDPOINT, json=payload("another key AKIAIOSFODNN7EXAMPLE"))

    assert event_store.entity_counts().get("AWS_ACCESS_KEY_ID") == 2


def test_recent_returns_newest_first_and_can_filter_by_user():
    client.post(ENDPOINT, json=payload("first", user_id="alice"))
    client.post(ENDPOINT, json=payload("second", user_id="bob"))

    assert len(event_store.recent()) == 2
    assert len(event_store.recent(user_id="alice")) == 1


def test_the_store_survives_a_message_with_no_findings():
    """The empty case is where ORM code usually breaks first."""
    client.post(ENDPOINT, json=payload("hello there"))

    with session.session_scope() as db:
        event = db.query(DecisionEvent).one()

    assert list(event.entities) == []
    assert json.loads(json.dumps(event.score)) == 0
