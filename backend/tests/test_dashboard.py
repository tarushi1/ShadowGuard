"""
Tests for the administrative dashboard.
================================================================

The first test is the one that matters, and it is the same test as the
one that opens `test_persistence.py`, asked one layer further out.

There, the question was "can a secret reach the disk?". Here it is
"can a secret reach a browser?" — because a page that renders the
store is a second chance to leak what the store was carefully designed
not to hold. If a column were ever added that contained prompt text,
persistence would catch it; but if a future template ever joined
against something else, or an aggregate ever grouped by a value taken
from user input, only this test would.

So it does not inspect the template or the context dictionary. It
sends a real secret through the real analysis endpoint, fetches the
real page, and searches the bytes the browser would receive.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.db import events as event_store
from backend.app.main import app

client = TestClient(app)

ANALYZE = "/api/v1/analyze"
DASHBOARD = "/dashboard"

# Distinctive enough that a substring search cannot match by accident,
# and each one is a value a real detector fires on — so each is
# guaranteed to travel the full path into the store rather than being
# dropped early as uninteresting.
SECRETS = {
    "aadhaar": "2345 6789 0123",
    "email": "zephyrine.quandt@example.org",
    "phone": "+91 98765 43210",
    "password": "correct-horse-battery-staple-77",
    "surrounding_prose": "quokka marmalade escarpment",
}


@pytest.fixture
def one_analysed_prompt():
    """Push a prompt carrying every secret above through the pipeline."""
    text = (
        f"Please help me draft a reply to the customer. Their Aadhaar is "
        f"{SECRETS['aadhaar']}, email {SECRETS['email']}, phone "
        f"{SECRETS['phone']}, and the account password is "
        f"{SECRETS['password']}. {SECRETS['surrounding_prose']}."
    )
    response = client.post(
        ANALYZE,
        json={"text": text, "user_id": "dashboard-test", "platform": "chatgpt"},
    )
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------
# The property the page must never break
# ---------------------------------------------------------------

def test_the_page_contains_no_fragment_of_the_prompt(one_analysed_prompt):
    """Not the secrets, and not the innocent words around them either.

    Checking the surrounding prose as well as the secrets is
    deliberate. A leak of "the text near the finding, for context" is
    the plausible, well-intentioned version of this bug, and it would
    slip past a test that only looked for card numbers.
    """
    page = client.get(DASHBOARD).text.lower()

    for label, secret in SECRETS.items():
        assert secret.lower() not in page, f"the dashboard rendered the {label}"

    # Also the digits alone, in case a template ever strips separators
    # while formatting.
    assert "234567890123" not in page.replace(" ", "")


def test_the_page_shows_that_something_was_detected(one_analysed_prompt):
    """The counterpart to the test above: prove it is not blank.

    A page that rendered nothing at all would pass every leak test
    perfectly, which is why "shows no content" and "shows something"
    have to be asserted together.
    """
    page = client.get(DASHBOARD).text

    assert "AADHAAR" in page          # the entity type, not the value
    assert "dashboard-test" in page   # who, which is metadata by design


# ---------------------------------------------------------------
# It has to survive the states a real install passes through
# ---------------------------------------------------------------

def test_it_renders_on_an_empty_database():
    """The first thing anyone sees after installing.

    Every chart scales to its own maximum, so an empty store is a
    division by zero waiting to happen — and the page that 500s would
    be precisely the page whose job is to say "nothing has happened
    yet".
    """
    response = client.get(DASHBOARD)

    assert response.status_code == 200
    assert "0" in response.text


def test_it_is_read_only():
    """No route on this page may change anything.

    `core/policy.py` is meant to be the only place policy lives. A
    dashboard with an "adjust threshold" button would quietly make it
    the second.
    """
    for method in (client.post, client.put, client.delete, client.patch):
        assert method(DASHBOARD).status_code == 405


def test_it_makes_no_external_requests():
    """No CDN, no font host, no analytics.

    A data-loss-prevention tool whose own console phones out is
    self-defeating, and the failure would be invisible: the page would
    look identical whether or not the request succeeded.
    """
    import re

    page = client.get(DASHBOARD).text

    # Any scheme-relative or absolute URL that is not loopback.
    offenders = [
        url for url in re.findall(r"""(?:https?:)?//[^\s"'<>)]+""", page)
        if "127.0.0.1" not in url and "localhost" not in url
    ]
    assert offenders == [], f"the dashboard reaches out to {offenders}"

    assert "<script src=" not in page
    assert "@import" not in page


# ---------------------------------------------------------------
# The aggregates behind it
# ---------------------------------------------------------------

def test_summary_counts_only_what_happened(one_analysed_prompt):
    summary = event_store.summary()

    assert summary["total"] == 1
    assert summary["users"] == 1
    assert 0.0 <= summary["intervention_rate"] <= 1.0


def test_histogram_buckets_span_the_whole_range(one_analysed_prompt):
    histogram = event_store.score_histogram(buckets=10)

    assert len(histogram) == 10
    assert sum(count for _, count in histogram) == 1
    # Every prompt lands in exactly one bucket, so no score may be lost
    # off either end — an off-by-one at 0 or 100 would silently drop
    # the two most interesting cases.
    assert [edge for edge, _ in histogram] == list(range(0, 100, 10))


def test_detector_counts_name_stages_not_values(one_analysed_prompt):
    counts = event_store.detector_counts()

    assert counts, "the prompt above fires several detectors"
    for name in counts:
        assert not any(ch.isdigit() for ch in name), f"{name!r} looks like data"
