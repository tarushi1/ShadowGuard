"""
Tests for the evaluation machinery itself.
================================================================

The harness produces the numbers the report is graded on, so a bug in
it is worse than a bug in a detector: a broken detector shows up as a
bad score, while a broken harness shows up as a GOOD one.

These tests cover the two pieces added in 4.3a — the entity registry
and entity-level metrics — plus the corpus loader's honesty rules.
"""

import pytest

from backend.app.detectors.catalog import ENTITY_SEVERITY, is_blocking, severity_of
from evaluation.corpus import load
from evaluation.metrics import entity_counts


# ---------------------------------------------------------------
# The entity registry
# ---------------------------------------------------------------

def test_every_rule_appears_in_the_catalog():
    """The registry is assembled from the detectors, not hand-copied.

    A hand-written table is correct the day it is written and wrong the
    first time a severity changes, with nothing anywhere to say so.
    """
    from backend.app.detectors import regex_detector, semantic_detector

    for rule in regex_detector.RULES:
        assert ENTITY_SEVERITY[rule.name] == rule.severity

    for entity in semantic_detector.SUPPORTED_ENTITIES:
        assert ENTITY_SEVERITY[entity] == semantic_detector.SEMANTIC_SEVERITY


def test_an_unknown_type_has_no_severity():
    """Returning None rather than a default is deliberate.

    A corpus expecting CREDIT_CARDS (plural, a typo) must fail loudly.
    Quietly assigning it a severity would let the typo through and
    report 0% recall for a reason unconnected to the detector.
    """
    assert severity_of("CREDIT_CARDS") is None
    assert severity_of("CREDIT_CARD") == "high"


def test_low_severity_types_are_not_blocking():
    """The distinction the corpus loader depends on."""
    assert not is_blocking("EMAIL")
    assert not is_blocking("PERSON")
    assert is_blocking("CREDIT_CARD")
    assert is_blocking("HIGH_ENTROPY_STRING")


# ---------------------------------------------------------------
# Corpus honesty rules
# ---------------------------------------------------------------

def _write(tmp_path, *rows):
    import json

    path = tmp_path / "corpus.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def _row(**overrides):
    row = {
        "id": "T001", "label": "benign", "category": "ordinary",
        "difficulty": "easy", "expected": [], "text": "hello",
    }
    row.update(overrides)
    return row


def test_a_benign_sample_may_expect_low_severity_entities(tmp_path):
    """The v2 change. `email me at x@y.com` genuinely contains an EMAIL.

    The old rule — benign implies expected is empty — conflated "found
    nothing" with "did nothing", which made every semantic finding
    unlabellable, since all of them are low severity and therefore live
    entirely inside benign samples.
    """
    path = _write(tmp_path, _row(expected=["EMAIL", "PERSON"]))
    assert load(path)[0].expected == ("EMAIL", "PERSON")


def test_a_benign_sample_may_not_expect_a_blocking_entity(tmp_path):
    """The honesty guarantee, preserved in a stronger form.

    You still cannot label a leak as benign — but it is now checked
    against the detectors' real severity table rather than against
    emptiness.
    """
    path = _write(tmp_path, _row(expected=["CREDIT_CARD"]))

    with pytest.raises(ValueError, match="labelled benign"):
        load(path)


def test_an_unknown_entity_type_fails_validation(tmp_path):
    path = _write(tmp_path, _row(label="sensitive", category="pii", expected=["CARD_NUMBER"]))

    with pytest.raises(ValueError, match="unknown entity type"):
        load(path)


def test_a_sensitive_sample_must_list_entities(tmp_path):
    path = _write(tmp_path, _row(label="sensitive", category="pii", expected=[]))

    with pytest.raises(ValueError, match="must list expected entities"):
        load(path)


def test_the_real_corpus_still_loads():
    """Cheap, and it catches a malformed line the moment it is added."""
    samples = load()

    assert len(samples) >= 150
    assert sum(s.is_sensitive for s in samples) == sum(not s.is_sensitive for s in samples), (
        "the corpus must stay balanced — see D22"
    )


# ---------------------------------------------------------------
# Entity-level metrics
# ---------------------------------------------------------------

def test_hits_misses_and_spurious_claims_are_counted_separately():
    counts = {
        c.entity_type: c
        for c in entity_counts(
            [
                (frozenset({"PERSON", "CREDIT_CARD"}), frozenset({"PERSON", "EMAIL"})),
                (frozenset({"PERSON"}), frozenset({"PERSON"})),
            ]
        )
    }

    assert (counts["PERSON"].found, counts["PERSON"].missed) == (2, 0)
    assert counts["CREDIT_CARD"].missed == 1
    assert counts["EMAIL"].spurious == 1


def test_recall_and_precision_answer_different_questions():
    """Found 1 of 2 present, and claimed it once where it was absent."""
    counts = entity_counts(
        [
            (frozenset({"PERSON"}), frozenset({"PERSON"})),
            (frozenset({"PERSON"}), frozenset()),
            (frozenset(), frozenset({"PERSON"})),
        ]
    )

    person = counts[0]
    assert person.recall == 0.5
    assert person.precision == 0.5


def test_a_type_never_expected_reports_zero_denominators():
    """Recall over an empty denominator must not be invented.

    The harness prints "-" for these rather than 0%, because a type the
    corpus never labels is UNLABELLED, not undetected.
    """
    counts = entity_counts([(frozenset(), frozenset({"ORGANIZATION"}))])

    assert counts[0].expected_total == 0
    assert counts[0].spurious == 1


def test_entity_level_sees_defects_the_action_level_hides():
    """The reason 4.3a exists.

    A sample expecting two entities, where one is found and one missed,
    is a MISS at entity level and a correct decision at action level —
    the message was still stopped. Both are true, and only reporting
    the second hides the gap.
    """
    counts = {
        c.entity_type: c
        for c in entity_counts([(frozenset({"AWS_ACCESS_KEY_ID", "EMAIL"}),
                                 frozenset({"AWS_ACCESS_KEY_ID"}))])
    }

    assert counts["AWS_ACCESS_KEY_ID"].recall == 1.0
    assert counts["EMAIL"].recall == 0.0
