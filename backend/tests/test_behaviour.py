"""
Tests for Stage 4 — the behavioural baseline.
================================================================

This stage is the easiest in the project to get subtly wrong, because
its failures are quiet: a baseline that never fires looks the same as
one that is working, and a baseline that fires on everyone looks like
strictness. The tests below pin the boundaries rather than the middle.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app.core import behaviour, risk
from backend.app.detectors.base import Finding
from backend.app.main import app
from backend.app.schemas.analyze import Action

client = TestClient(app)
ENDPOINT = "/api/v1/analyze"


def baseline(events=100, mean=200.0, stdev=50.0, seen=()):
    return behaviour.Baseline(
        user_id="u", events=events, mean_chars=mean,
        stdev_chars=stdev, seen_entity_types=frozenset(seen),
    )


# ---------------------------------------------------------------
# Cold start — the boundary that protects new users
# ---------------------------------------------------------------

def test_a_user_with_no_history_is_never_penalised():
    """A system that reads "no baseline" as "suspicious" punishes
    everyone on their first day.

    With no evidence, add no evidence.
    """
    signal = behaviour.assess(
        baseline(events=0, mean=0, stdev=0),
        char_count=50_000,
        entity_types=frozenset({"CREDIT_CARD", "US_SSN"}),
    )

    assert signal.is_neutral


def test_a_baseline_below_the_minimum_is_not_used():
    """Below MIN_HISTORY the standard deviation of a handful of samples
    is noise, and a z-score computed from it swings wildly on the next
    message."""
    signal = behaviour.assess(
        baseline(events=behaviour.MIN_HISTORY - 1),
        char_count=9_000,
        entity_types=frozenset({"CREDIT_CARD"}),
    )

    assert signal.is_neutral


def test_a_single_event_cannot_produce_a_zero_divisor():
    """`pstdev` of one value is 0. Dividing by it would make every
    subsequent message infinitely unusual."""
    signal = behaviour.assess(
        baseline(events=100, stdev=0.0), char_count=9_000, entity_types=frozenset()
    )

    assert signal.multiplier == 1.0


# ---------------------------------------------------------------
# The signals
# ---------------------------------------------------------------

def test_an_unusually_large_message_raises_the_score():
    """The bulk-export case: someone who normally sends 200 characters
    pasting 9000 is doing something different, whatever it contains."""
    signal = behaviour.assess(baseline(), char_count=9_000, entity_types=frozenset())

    assert signal.multiplier > 1.0
    assert any("unusually large" in r for r in signal.reasons)


def test_an_ordinary_message_from_an_established_user_is_neutral():
    """The common case must cost nothing. If a baseline fires on
    routine traffic it is noise with extra steps."""
    signal = behaviour.assess(
        baseline(seen={"EMAIL"}), char_count=210, entity_types=frozenset({"EMAIL"})
    )

    assert signal.is_neutral


def test_an_entity_type_never_sent_before_raises_the_score():
    """The most explainable signal, and the one a user can act on.

    "You have never sent a payment card before" means something;
    "your z-score is 3.2" does not.
    """
    signal = behaviour.assess(
        baseline(seen={"EMAIL", "PERSON"}),
        char_count=210,
        entity_types=frozenset({"CREDIT_CARD"}),
    )

    assert signal.multiplier > 1.0
    assert any("first time" in r for r in signal.reasons)


def test_amplification_is_capped():
    """Behaviour is CIRCUMSTANTIAL. It may raise the priority of a real
    finding; it must never dominate the content signal."""
    signal = behaviour.assess(
        baseline(seen=set()), char_count=100_000, entity_types=frozenset({"A", "B", "C"})
    )

    assert signal.multiplier <= behaviour.MAX_AMPLIFICATION


# ---------------------------------------------------------------
# The property that keeps this from becoming a second detector
# ---------------------------------------------------------------

def test_behaviour_cannot_manufacture_a_verdict_from_nothing():
    """THE most important test in this file.

    A message with no findings scores zero, and zero times any
    multiplier is still zero. If behaviour could create a verdict
    alone, it would be a detector that no corpus can measure — and
    "unusual" would start masquerading as "sensitive", which are
    different claims.
    """
    extreme = behaviour.BehaviourSignal(multiplier=99.0, reasons=("very unusual",))
    result = risk.assess([], "how do I reverse a string in python", behaviour=extreme)

    assert result.action is Action.ALLOW
    assert result.score == 0


def test_the_evaluation_path_is_unaffected():
    """The harness calls `assess` with no signal, and must keep getting
    exactly the content-only verdict. If behaviour leaked into the
    default path, every corpus number would silently become a claim
    about user history instead of about detection."""
    findings = [Finding(
        type="CREDIT_CARD", start=7, end=23, score=0.9,
        detector="regex", severity="high", value="x",
    )]
    text = "charge 4532015112830366 for the invoice"

    assert risk.assess(findings, text).score == risk.assess(findings, text, behaviour=None).score


# ---------------------------------------------------------------
# End to end
# ---------------------------------------------------------------

def test_the_same_message_scores_higher_for_a_user_it_is_unusual_for():
    """The whole point of the stage, through the real endpoint."""
    for i in range(behaviour.MIN_HISTORY + 5):
        client.post(ENDPOINT, json={
            "text": f"how do I fix this TypeError in module {i}",
            "user_id": "established", "platform": "chatgpt",
        })

    card = "charge 4532015112830366 for the invoice please"
    stranger = client.post(ENDPOINT, json={
        "text": card, "user_id": "brand-new", "platform": "chatgpt"}).json()
    known = client.post(ENDPOINT, json={
        "text": card, "user_id": "established", "platform": "chatgpt"}).json()

    assert known["score"] > stranger["score"], "history made no difference"
    assert any("first time" in r for r in known["reasons"])


def test_a_broken_store_does_not_break_detection():
    """A behavioural signal is an enhancement. If the store is
    unavailable the message must still be judged on its content."""
    def explode(*_a, **_k):
        raise RuntimeError("database gone")

    original = behaviour.build_baseline
    behaviour.build_baseline = explode
    try:
        signal = behaviour.signal_for("u", char_count=100, entity_types=frozenset())
    finally:
        behaviour.build_baseline = original

    assert signal.is_neutral
