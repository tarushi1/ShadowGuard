"""
Tests for redaction.
================================================================

Redaction is the only place in the system where the server sends text
BACK to the client, so its invariants are security invariants rather
than formatting preferences. The first two tests here are the ones
that matter: no value survives, and no offset arithmetic goes wrong.
"""

from backend.app.core import redactor
from backend.app.detectors.base import Finding


def finding(entity_type, start, end, value="") -> Finding:
    return Finding(
        type=entity_type, start=start, end=end, score=0.9,
        detector="test", severity="high", value=value,
    )


# ---------------------------------------------------------------
# The invariant that matters
# ---------------------------------------------------------------

def test_no_character_of_the_secret_survives():
    """The whole point, and the reason partial masking was rejected.

    `4532 **** **** 0366` is familiar from banking UIs and wrong here:
    the issuer prefix plus the last four digits is enough to identify a
    card in most customer-service systems. Partial masking is
    disclosure wearing redaction's clothes.
    """
    secret = "4532015112830366"
    text = f"charge {secret} for the invoice"

    output = redactor.redact(text, [finding("CREDIT_CARD", 7, 7 + len(secret))])

    assert secret not in output
    for fragment in (secret[:4], secret[-4:]):
        assert fragment not in output, f"{fragment} leaked through"


def test_the_placeholder_names_the_type():
    """A bare [REDACTED] loses the context that makes the reply useful:
    the model cannot tell whether it is being asked about a key or a
    name. The type leaks nothing about the value."""
    output = redactor.redact("my key is X", [finding("OPENAI_API_KEY", 10, 11)])

    assert output == "my key is [REDACTED: OPENAI_API_KEY]"


def test_the_surrounding_message_is_preserved_exactly():
    text = "please charge SECRET today, thanks"
    output = redactor.redact(text, [finding("CREDIT_CARD", 14, 20)])

    assert output.startswith("please charge ")
    assert output.endswith(" today, thanks")


# ---------------------------------------------------------------
# Offset arithmetic
# ---------------------------------------------------------------

def test_multiple_findings_all_land_in_the_right_place():
    """Replacing left-to-right shifts every offset after the first.

    It looks correct on a single finding and corrupts everything after
    it — an off-by-a-growing-amount bug, which is why the
    implementation builds right-to-left.
    """
    text = "keys AAAA and BBBB and CCCC end"
    output = redactor.redact(
        text,
        [
            finding("K1", 5, 9),
            finding("K2", 14, 18),
            finding("K3", 23, 27),
        ],
    )

    assert output == (
        "keys [REDACTED: K1] and [REDACTED: K2] and [REDACTED: K3] end"
    )


def test_overlapping_findings_do_not_corrupt_each_other():
    """`resolve_overlaps` usually runs first, but this must not depend
    on it: two overlapping spans replaced independently would corrupt
    each other's offsets, and the corruption shows up as mangled text
    rather than as an error."""
    text = "value ABCDEFGH end"
    output = redactor.redact(text, [finding("A", 6, 12), finding("B", 9, 14)])

    assert "ABCDEFGH" not in output
    assert output.startswith("value ")
    assert output.endswith(" end")


def test_findings_given_out_of_order_are_handled():
    text = "aaa BBB ccc DDD eee"
    output = redactor.redact(text, [finding("Y", 12, 15), finding("X", 4, 7)])

    assert output == "aaa [REDACTED: X] ccc [REDACTED: Y] eee"


def test_no_findings_returns_the_text_unchanged():
    assert redactor.redact("nothing here", []) == "nothing here"


# ---------------------------------------------------------------
# Coverage — the block/redact decision
# ---------------------------------------------------------------

def test_coverage_is_measured_over_non_whitespace():
    """A message padded with newlines is not more redactable than the
    same message without them."""
    text = "AAAA\n\n\n\n\n\n\n\n\n\nBB"
    assert redactor.coverage(text, [finding("X", 0, 4)]) == 4 / 6


def test_a_message_that_is_entirely_a_secret_has_full_coverage():
    key = "AKIAIOSFODNN7EXAMPLE"

    assert redactor.coverage(key, [finding("AWS_ACCESS_KEY_ID", 0, len(key))]) == 1.0


def test_a_secret_inside_a_sentence_has_low_coverage():
    text = "please rotate the deploy key AKIAIOSFODNN7EXAMPLE before friday if you can"
    covered = redactor.coverage(text, [finding("AWS_ACCESS_KEY_ID", 29, 49)])

    assert covered < 0.5


def test_coverage_of_nothing_is_zero():
    assert redactor.coverage("some text", []) == 0.0
    assert redactor.coverage("", []) == 0.0
