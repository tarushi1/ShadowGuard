"""
Tests for the risk engine.
================================================================

This module decides what happens to every message, so its failure mode
is either "a leak went through" or "a control nobody trusts". Both are
worth pinning down precisely.

The tests are written against the ENGINE, not against the corpus. The
harness measures aggregate behaviour; these state the individual rules
the aggregate is supposed to come from, so a future tuning pass that
improves a number by breaking a principle fails here rather than
silently shipping.
"""

from dataclasses import replace

import pytest

from backend.app.core import risk
from backend.app.core.policy import DEFAULT_POLICY
from backend.app.detectors.base import Finding
from backend.app.schemas.analyze import Action


def finding(entity_type, severity, *, score=0.9, start=0, end=10, value="x") -> Finding:
    return Finding(
        type=entity_type, start=start, end=end, score=score,
        detector="test", severity=severity, value=value,
    )


def assess(findings, text="some text with a placeholder span in it"):
    return risk.assess(findings, text)


# ---------------------------------------------------------------
# The behaviour the max-severity placeholder already had
# ---------------------------------------------------------------

def test_nothing_found_is_allowed():
    result = assess([])

    assert result.action is Action.ALLOW
    assert result.score == 0


def test_a_critical_finding_never_passes():
    """Whether it blocks or offers redaction is policy; that it stops
    the message is not."""
    result = assess([finding("PRIVATE_KEY", "critical", score=1.0)])

    assert result.action in (Action.BLOCK, Action.REDACT)


def test_a_message_that_is_nothing_but_the_secret_is_blocked():
    """Redaction would hand the user `[REDACTED: PRIVATE_KEY]`, which
    is a receipt rather than a message.

    This is what keeps `block` meaningful instead of vestigial: it now
    means "there is nothing here except the thing we cannot send".
    """
    key = "AKIAIOSFODNN7EXAMPLE"
    result = assess(
        [finding("AWS_ACCESS_KEY_ID", "critical", score=0.97, start=0, end=len(key))], key
    )

    assert result.action is Action.BLOCK


def test_every_entropy_finding_can_warn_and_none_can_block():
    """D19's design intent, asserted rather than assumed.

    This is what the `medium` weight is DERIVED from. The first version
    used 35, which put the warn line at confidence 0.71 — through the
    middle of the entropy distribution — so half of all entropy
    findings silently stopped warning and two true positives were lost.
    """
    for confidence in (0.50, 0.65, 0.75, 0.85):
        result = assess([finding("HIGH_ENTROPY_STRING", "medium", score=confidence)])

        assert result.action is Action.WARN, f"confidence {confidence} failed to warn"


# ---------------------------------------------------------------
# What the placeholder could not do
# ---------------------------------------------------------------

def test_a_name_alone_is_not_sensitive():
    """People discuss colleagues, authors and public figures all day."""
    result = assess([finding("PERSON", "low", score=0.85)], "write a biography of Ada Lovelace")

    assert result.action is Action.ALLOW


def test_a_name_plus_a_contact_address_is_sensitive():
    """The case the max-severity policy structurally could not express.

    A name identifies nobody in particular. An email address alone
    identifies nobody in particular. Together they are a specific,
    contactable human being — personal data under GDPR in a way that
    neither half is.
    """
    text = "Miriam Feldstein can be reached at m.f@example.com about the contract"
    result = assess(
        [
            finding("PERSON", "low", score=0.85, start=0, end=16),
            finding("EMAIL", "low", score=0.95, start=35, end=50),
        ],
        text,
    )

    assert result.action is not Action.ALLOW
    assert any("contact" in reason for reason in result.reasons)


def test_counting_findings_is_not_fusion():
    """Two low findings must NOT warn on count alone.

    Measured on the corpus, warning at two-or-more findings catches 8
    of the 11 misses and costs 13 benign false positives, including
    `explain how Satya Nadella restructured Microsoft`. This test is
    what stops that shortcut being reintroduced as a simplification.
    """
    result = assess(
        [
            finding("PERSON", "low", score=0.85, start=11, end=24),
            finding("ORGANIZATION", "low", score=0.85, start=38, end=47),
        ],
        "explain how Satya Nadella restructured Microsoft around cloud services",
    )

    assert result.action is Action.ALLOW


def test_a_data_subject_marker_turns_the_same_entities_sensitive():
    """The other half of the test above, and the whole design in one pair.

    Identical entity types, identical severities, opposite verdicts.
    The difference is whether the person is a subject of a record or a
    topic of discussion — D29's used-vs-discussed distinction, applied
    to fusion instead of detection.
    """
    result = assess(
        [finding("PERSON", "low", score=0.85, start=11, end=27)],
        "our client Deborah Alvarez is threatening to sue over the denied claim",
    )

    assert result.action is Action.WARN
    assert any("data subject" in reason for reason in result.reasons)


def test_only_people_are_data_subjects():
    """Regression for a false positive caused by a conceptual error.

    Amplifying ORGANIZATION on a marker word flagged:

        "our test fixtures all use Foo Barrington as the customer name"

    A data subject is a natural person. Organisations and places are
    not, so a marker near an ORGANIZATION says nothing about personal
    data.
    """
    result = assess(
        [finding("ORGANIZATION", "low", score=0.85, start=26, end=39)],
        "our test fixtures all use Foo Barrington as the customer name",
    )

    assert result.action is Action.ALLOW


def test_a_marker_beyond_the_window_does_not_amplify():
    """Proximity is the point, exactly as it was in D29."""
    text = "our client is happy. " + ("filler words here. " * 8) + "Deborah Alvarez"
    start = text.index("Deborah")

    result = assess([finding("PERSON", "low", score=0.85, start=start, end=len(text))], text)

    assert result.action is Action.ALLOW


def test_a_finding_cannot_supply_its_own_context():
    """A sender who names their company `Patient Records Ltd` must not
    be able to raise their own score, or lower it."""
    text = "customer"
    result = assess([finding("PERSON", "low", score=0.85, start=0, end=len(text))], text)

    assert result.action is Action.ALLOW


# ---------------------------------------------------------------
# Scoring mechanics
# ---------------------------------------------------------------

def test_repeats_of_one_type_have_diminishing_returns():
    """Ten addresses in a mailing list are one fact, not ten.

    Without decay, volume substitutes for severity and a message is
    judged by how many times a detector fired.
    """
    one = assess([finding("EMAIL", "low", start=0, end=5)]).score
    many = assess([finding("EMAIL", "low", start=i * 10, end=i * 10 + 5) for i in range(10)]).score

    assert many < one * 3, "ten emails should not outweigh three distinct facts"


def test_a_combination_bonus_is_paid_once_per_class_pair():
    """Three addresses beside one name is still one linkable identity."""
    single = assess(
        [finding("PERSON", "low", start=0, end=5), finding("EMAIL", "low", start=10, end=20)]
    ).score
    triple = assess(
        [finding("PERSON", "low", start=0, end=5)]
        + [finding("EMAIL", "low", start=10 + i * 20, end=20 + i * 20) for i in range(3)]
    ).score

    assert triple - single < DEFAULT_POLICY.combination_bonuses[("contact", "identity")]


def test_the_score_is_clamped_to_the_declared_range():
    """`score` is declared 0-100 in the API schema, so a sum that runs
    past 100 would fail validation at the boundary rather than here."""
    result = assess([finding(f"T{i}", "critical", score=1.0, start=i * 10) for i in range(5)])

    assert 0 <= result.score <= 100


def test_redaction_is_offered_when_it_resolves_the_risk():
    """A card inside a sentence: remove it and the question survives."""
    text = "charge 4532015112830366 for the invoice please"
    result = assess([finding("CREDIT_CARD", "high", start=7, end=23)], text)

    assert result.action is Action.REDACT


def test_redaction_is_not_offered_when_it_would_not_resolve_the_risk():
    """Offering to redact and stopping the user anyway is the worst of
    both: their text is damaged AND they are still blocked.

    Here nothing is removable — a marked data subject is not — so the
    correct answer is to warn and let the human decide.
    """
    text = "our client Deborah Alvarez is threatening to sue over the denied claim"
    result = assess([finding("PERSON", "low", score=0.85, start=11, end=27)], text)

    assert result.action is Action.WARN


def test_redaction_removes_only_what_the_decision_was_based_on():
    """Regression for a message that redaction destroyed.

    The decision to redact was justified by removing the EMAIL alone —
    the residual score with the name still present was below the warn
    threshold. Passing every finding to the redactor instead produced

        "send the contract to [REDACTED: PERSON] at
         [REDACTED: ORGANIZATION], her address is [REDACTED: EMAIL]"

    which is not a redacted message, it is a destroyed one.
    """
    from backend.app.core import redactor, risk

    text = "send the contract to Miriam Feldstein, her address is m.f@example.com"
    findings = [
        finding("PERSON", "low", score=0.85, start=21, end=37),
        finding("EMAIL", "low", score=0.95, start=54, end=69),
    ]

    output = redactor.redact(text, risk.removable(findings))

    assert "Miriam Feldstein" in output, "the name should have survived"
    assert "m.f@example.com" not in output
    assert "[REDACTED: EMAIL]" in output


# ---------------------------------------------------------------
# Policy is data
# ---------------------------------------------------------------

def test_thresholds_are_changeable_without_touching_code():
    """The claim the report makes about `policy.py`, asserted.

    "Block on critical" is an organisational choice, not a technical
    fact. A bank and a startup can run identical detectors and disagree
    about it.
    """
    strict = replace(DEFAULT_POLICY, warn_threshold=1.0)
    findings = [finding("PERSON", "low", score=0.85)]

    assert risk.assess(findings, "a name", DEFAULT_POLICY).action is Action.ALLOW
    assert risk.assess(findings, "a name", strict).action is Action.WARN


def test_every_combination_bonus_has_a_sentence_a_user_can_read():
    """A bonus without a reason raises the score and explains nothing,
    which is the state that makes people distrust a control."""
    from backend.app.core.risk import _COMBINATION_REASONS

    missing = set(DEFAULT_POLICY.combination_bonuses) - set(_COMBINATION_REASONS)
    assert not missing, f"combinations with no explanation: {sorted(missing)}"


def test_every_entity_type_has_a_class():
    """A type missing from CLASS_OF silently drops out of every
    combination rule, which is a failure that shows up as slightly
    worse recall and nothing else."""
    from backend.app.core.policy import CLASS_OF
    from backend.app.detectors.catalog import ENTITY_SEVERITY

    missing = set(ENTITY_SEVERITY) - set(CLASS_OF)
    assert not missing, f"unclassified entity types: {sorted(missing)}"


# ---------------------------------------------------------------
# Data-subject vocabulary
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text, name_at",
    [
        ("account holder Rahul Sharma has a balance of 45000", (15, 27)),
        ("Sarah Chen was diagnosed with type 2 diabetes", (0, 10)),
        ("borrower Daniel Osei defaulted on the instalment", (9, 20)),
        ("the defendant Marcus Delgado filed a counterclaim", (14, 28)),
        ("draft the exit interview for Aditya Shukla", (29, 43)),
        ("referral for Rakesh Menon to cardiology", (13, 25)),
        ("the policyholder Meera Iyer has not renewed", (17, 27)),
        ("the tenant Priya Menon is in arrears", (11, 22)),
    ],
)
def test_relationship_language_from_any_department_is_recognised(text, name_at):
    """The marker list was written from HR and support vocabulary only.

    A user found that `our client Rahul Sharma` was caught while
    `account holder Rahul Sharma` was not — the same relationship,
    named by a different department. Banking, clinical, legal and
    insurance language now sit alongside the original set.

    Baseline before widening: 4 of 14 realistic phrasings caught.
    """
    start, end = name_at
    result = assess([finding("PERSON", "low", score=0.85, start=start, end=end)], text)

    assert result.action is not Action.ALLOW, f"not recognised as a data subject: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "the average salary for a backend developer in bangalore",
        "I diagnosed the issue as a connection pool leak",
        "how do I file a claim against a null pointer",
        "our compensation library handles retries automatically",
    ],
)
def test_marker_words_alone_prove_nothing(text):
    """A marker with no person attached is just a word.

    Widening the vocabulary is only safe because amplification requires
    an `identity` finding to amplify. `salary`, `diagnosed`, `claim`
    and `compensation` all occur in ordinary technical prose.
    """
    from backend.app.pipeline import orchestrator

    assert orchestrator.run_stages(text).action is Action.ALLOW, f"false positive on: {text}"


def test_a_public_figure_is_still_not_a_data_subject():
    """The property the widening must not break.

    Every word added is a chance to catch an ordinary sentence about a
    well-known person. The distinction is relationship language, not
    the presence of a name.
    """
    for text, span in [
        ("explain how Satya Nadella restructured Microsoft", (12, 25)),
        ("write a short biography of Ada Lovelace", (27, 39)),
        ("what did Alan Turing contribute to computing", (9, 20)),
    ]:
        result = assess([finding("PERSON", "low", score=0.85, start=span[0], end=span[1])], text)
        assert result.action is Action.ALLOW, f"false positive on: {text}"
