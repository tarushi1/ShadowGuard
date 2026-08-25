"""
Stage 2 — the semantic detector.
================================================================

This file did not exist until the capitalisation filter had to change,
which is worth admitting rather than quietly fixing: Stage 2 was
covered only through the corpus and the endpoint tests. Both measure
whether the SYSTEM behaves, and neither pins the small decisions inside
this module — so the rule that rejects `kyc` as a person had no test
naming it, and would have been silently removable.

Most of what is asserted here is about ONE function, `_looks_like_a_name`,
because that is where every judgement call in the module lives. The
model itself is not under test: spaCy's accuracy is spaCy's problem,
and the corpus measures it end to end.
"""

import pytest

from backend.app.detectors import semantic_detector as sem


def types_in(text: str) -> set[str]:
    return {f.type for f in sem.scan(text)}


def persons_in(text: str) -> list[str]:
    return [f.value for f in sem.scan(text) if f.type == "PERSON"]


# ---------------------------------------------------------------
# The rule the filter was built for
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "kyc document shows aadhaar 2345 6789 0123",
        "kyc documents are attached for review",
    ],
)
def test_a_lowercase_acronym_is_not_a_person(text):
    """spaCy labels `kyc` PERSON at 0.85 — too high for any threshold.

    The model is pattern-matching on sentence position, and a short
    lowercase acronym there genuinely looks like a name to it.
    """
    assert "kyc" not in [p.lower() for p in persons_in(text)]


def test_a_span_that_is_only_a_marker_is_refused():
    """A finding may never supply its own justification.

    `kyc` is itself a data-subject marker, so a naive "is there a
    marker nearby?" test would re-admit exactly the bug above. It does
    not, because `context_window` excludes the span being judged — the
    same property D29's context gates depend on.
    """
    assert sem._looks_like_a_name("kyc", "kyc document shows aadhaar", 0, 3) is False


# ---------------------------------------------------------------
# Capitalisation, and the exception to it
# ---------------------------------------------------------------

def test_a_capitalised_name_needs_no_context():
    assert persons_in("Sarah Chen approved the refund") == ["Sarah Chen"]


def test_a_lowercase_name_beside_a_marker_is_admitted():
    """The case a user found by typing their own name in lowercase.

    `customer john, phone 801-555-0142` is a customer record, and the
    capitalisation rule was throwing away the only signal saying so.
    """
    assert sem._looks_like_a_name("john", "customer john, phone 801-555-0142", 9, 13) is True


def test_a_marker_swallowed_by_the_span_still_counts():
    """spaCy sometimes tags "patient priya" as one PERSON.

    The marker then sits INSIDE the span, `context_window` correctly
    excludes it, and a patient's name beside a phone number scored 6.
    A marker plus a residual means the residual is the name.
    """
    text = "patient priya, phone 9876543210"
    assert sem._looks_like_a_name("patient priya", text, 0, 13) is True

    assert "PERSON" in types_in(text)


def test_a_lowercase_span_with_no_marker_is_still_refused():
    """A documented limitation, pinned so that changing it is deliberate.

    `john is from Utah` yields no PERSON. That is the accepted cost of
    the capitalisation rule — and it does not matter much, because a
    name beside a place is not a record and scores below the warn
    threshold either way.
    """
    assert sem._looks_like_a_name("john", "john is from Utah", 0, 4) is False


@pytest.mark.parametrize("span", ["", " ", "a", "x"])
def test_a_span_too_short_to_be_a_name_is_refused(span):
    assert sem._looks_like_a_name(span, f"customer {span} here", 9, 9 + len(span)) is False


# ---------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------

def test_presidio_does_not_report_what_stage_1_owns():
    """Restricted by ALLOWLIST rather than de-duplicated afterwards.

    Presidio ships recognizers for email, cards and SSNs that duplicate
    ours. Avoiding the collision by construction means a future
    Presidio release adding a recognizer cannot silently start
    double-counting.
    """
    found = types_in("email sarah@acme.com card 4111 1111 1111 1111 ssn 123-45-6789")

    for owned_by_stage_1 in ("EMAIL_ADDRESS", "CREDIT_CARD", "US_SSN", "IBAN_CODE"):
        assert owned_by_stage_1 not in found


def test_every_finding_is_low_severity():
    """A name is not a secret; combination is what makes it sensitive.

    Encoding that here would put fusion logic inside a detector, where
    the harness cannot vary it and the report cannot compare it.
    """
    findings = sem.scan("Sarah Chen from Acme in Bangalore")

    assert findings
    assert all(f.severity == "low" for f in findings)


def test_empty_input_does_not_load_the_model():
    assert sem.scan("") == []
    assert sem.scan("   \n ") == []
