"""
Tests for Stage 1a — the pattern detector.
================================================================

Structured deliberately around the two ways a detector fails:

  POSITIVE cases  → recall. What we miss is a security failure.
  NEGATIVE cases  → precision. What we wrongly flag is an ADOPTION
                    failure, and a tool people disable has a recall
                    of zero.

The negative cases carry as much weight as the positive ones. It is
easy to write a detector that catches everything by flagging
everything, and the negative tests are what stop this module drifting
in that direction.

The secrets below are syntactically valid and completely fake.
"""

import pytest

from backend.app.detectors.regex_detector import (
    RULES,
    highest_severity,
    luhn_valid,
    scan,
)


def types_in(text: str) -> set[str]:
    return {f.type for f in scan(text)}


# ---------------------------------------------------------------
# POSITIVE — recall
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "expected, text",
    [
        ("OPENAI_API_KEY", "use sk-proj-Xk92mNvQ8wRt5YuI3oPa7SdF1gHj4KlZ for the demo"),
        ("OPENAI_API_KEY", "key: sk-Xk92mNvQ8wRt5YuI3oPa7SdF1gHj"),
        ("ANTHROPIC_API_KEY", "sk-ant-api03-Xk92mNvQ8wRt5YuI3oPa7SdF1gHj4KlZ"),
        ("AWS_ACCESS_KEY_ID", "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"),
        ("AWS_ACCESS_KEY_ID", "temporary creds ASIAIOSFODNN7EXAMPLE expire soon"),
        ("GITHUB_TOKEN", "ghp_1234567890abcdefghijklmnopqrstuvwxyz"),
        ("GITHUB_PAT", "github_pat_" + "A" * 55),
        ("SLACK_TOKEN", "xoxb-123456789012-abcdefghijklmnop"),
        ("GOOGLE_API_KEY", "AIzaSyD-1234567890abcdefghijklmnopqrstu"),
        ("STRIPE_SECRET_KEY", "sk_live_51H8xKlMnOpQrStUvWxYz1234"),
        ("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow..."),
        ("PRIVATE_KEY", "-----BEGIN OPENSSH PRIVATE KEY-----"),
        ("US_SSN", "his ssn is 123-45-6789 apparently"),
        ("INDIA_PAN", "PAN: ABCDE1234F"),
        ("EMAIL", "reach me at arushi@example.com tomorrow"),
    ],
)
def test_detects(expected, text):
    assert expected in types_in(text)


def test_detects_a_jwt():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert "JWT" in types_in(f"Authorization: Bearer {token}")


@pytest.mark.parametrize(
    "card",
    [
        "4111111111111111",       # Visa test number
        "5500005555555559",       # Mastercard test number
        "4111 1111 1111 1111",    # spaced
        "4111-1111-1111-1111",    # hyphenated
    ],
)
def test_detects_valid_card_numbers(card):
    assert "CREDIT_CARD" in types_in(f"charge it to {card} please")


def test_finds_multiple_distinct_secrets_in_one_message():
    text = (
        "deploy with AKIAIOSFODNN7EXAMPLE and "
        "ghp_1234567890abcdefghijklmnopqrstuvwxyz, "
        "ping arushi@example.com when done"
    )
    assert types_in(text) == {"AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "EMAIL"}


# ---------------------------------------------------------------
# NEGATIVE — precision
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "how do I sort a list of dictionaries in python",
        "can you explain the difference between TCP and UDP",
        "write a unit test for this function please",
        "what does the sk parameter mean in scikit-learn",
        "my password manager keeps logging me out",
        "the meeting is at 10:30 in room 4-2-1",
        "order 1234567890 shipped on 2026-07-29",
        "",
        "     ",
    ],
)
def test_ordinary_text_produces_nothing(text):
    """Every one of these would be a false positive in production.

    A tool that flags "what does the sk parameter mean in
    scikit-learn" gets switched off within a day, at which point its
    recall is zero and it protects nothing.
    """
    assert scan(text) == []


@pytest.mark.parametrize(
    "number",
    [
        "1234567890123456",   # sixteen digits, fails Luhn
        "4111111111111112",   # one digit off a valid card
        "9999999999999999",   # fails Luhn
        "0000000000000000",   # passes Luhn but is obviously a placeholder
    ],
)
def test_digit_strings_that_are_not_cards_are_rejected(number):
    """This is the Luhn validator earning its place.

    Without it every order number, invoice id and tracking code in
    existence is a credit card. Roughly nine out of ten random
    16-digit strings fail the checksum, so one validator function cuts
    this rule's false positives by about an order of magnitude.
    """
    assert "CREDIT_CARD" not in types_in(f"reference {number} for the order")


@pytest.mark.parametrize(
    "text",
    [
        "sk-",
        "sk-short",
        "the sk-learn library",
    ],
)
def test_partial_key_prefixes_are_not_keys(text):
    assert "OPENAI_API_KEY" not in types_in(text)


def test_stripe_test_keys_are_deliberately_ignored():
    """A test key is not a secret, and flagging it is actively harmful.

    Warnings that fire on harmless things teach people to dismiss
    warnings — and dismissing warnings is how real leaks get through.
    """
    assert types_in("sk_test_51H8xKlMnOpQrStUvWxYz1234") == set()


@pytest.mark.parametrize("ssn", ["000-45-6789", "666-45-6789", "900-45-6789", "123-00-6789"])
def test_never_issued_ssn_ranges_are_excluded(ssn):
    """The negative lookaheads remove a large class of formatted
    numbers that merely look like an SSN."""
    assert "US_SSN" not in types_in(f"ref {ssn}")


# ---------------------------------------------------------------
# Overlap resolution
# ---------------------------------------------------------------

def test_one_secret_is_reported_once():
    """A JWT is base64, so several patterns match fragments of it.

    Without overlap resolution one secret produces three findings, the
    modal shows three chips for one problem, and any count-based score
    is inflated by nothing more than how many rules happened to fire.
    """
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    findings = scan(token)

    assert len(findings) == 1
    assert findings[0].type == "JWT"


def test_a_spaced_card_is_not_reported_as_an_aadhaar_number():
    """Regression. This was a real bug, found by the test above.

    The Aadhaar pattern matches any standalone twelve digits starting
    2-9, which includes the first twelve digits of a sixteen-digit
    card. Because overlap resolution originally ranked SEVERITY before
    confidence, a 0.7-confidence Aadhaar guess beat a Luhn-VALIDATED
    0.9-confidence card and the message read "national id detected".

    Two fixes: lookarounds so Aadhaar cannot match inside a longer
    digit run, and confidence-first ranking. Severity says how bad a
    finding would be if true; confidence says how likely it is to be
    true. Choosing between two readings of the same characters is a
    question about truth, not consequence.
    """
    for card in ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111"):
        found = types_in(f"charge {card} please")
        assert found == {"CREDIT_CARD"}, f"{card} produced {found}"


def test_a_standalone_aadhaar_is_still_detected():
    """The lookarounds must tighten the rule, not disable it."""
    assert "INDIA_AADHAAR" in types_in("aadhaar 2345 6789 0123 on file")


def test_findings_are_returned_in_document_order():
    text = "email arushi@example.com then use AKIAIOSFODNN7EXAMPLE"
    findings = scan(text)

    assert [f.start for f in findings] == sorted(f.start for f in findings)


# ---------------------------------------------------------------
# Offsets and values
# ---------------------------------------------------------------

def test_offsets_point_at_the_actual_match():
    """The extension highlights text[start:end], so an off-by-one here
    shows the user the wrong characters."""
    key = "AKIAIOSFODNN7EXAMPLE"
    text = f"the key is {key} okay"

    finding = next(f for f in scan(text) if f.type == "AWS_ACCESS_KEY_ID")

    assert text[finding.start : finding.end] == key
    assert finding.value == key


def test_severity_ranking():
    assert highest_severity(scan("arushi@example.com")) == "low"
    assert highest_severity(scan("AKIAIOSFODNN7EXAMPLE")) == "critical"
    assert highest_severity(scan("nothing here")) is None


def test_a_critical_finding_outranks_a_low_one_in_the_same_message():
    text = "mail arushi@example.com the key AKIAIOSFODNN7EXAMPLE"
    assert highest_severity(scan(text)) == "critical"


# ---------------------------------------------------------------
# Luhn, directly
# ---------------------------------------------------------------

@pytest.mark.parametrize("number", ["4111111111111111", "5500005555555559", "378282246310005"])
def test_luhn_accepts_valid_numbers(number):
    assert luhn_valid(number)


@pytest.mark.parametrize("number", ["4111111111111112", "1234567890123456", "123", ""])
def test_luhn_rejects_invalid_numbers(number):
    assert not luhn_valid(number)


# ---------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------

def test_every_rule_has_a_distinct_name():
    """Duplicate names would silently merge two rules in the UI and in
    any per-type metric the evaluation harness reports."""
    names = [rule.name for rule in RULES]
    assert len(names) == len(set(names))


def test_large_input_completes_quickly():
    """A guard against catastrophic backtracking (ReDoS).

    Nested quantifiers can make a regex take exponential time, so a
    short input hangs the server for minutes. Every pattern here uses
    bounded quantifiers; this test fails loudly if someone adds one
    that does not.
    """
    import time

    text = ("lorem ipsum dolor sit amet 1234-5678-9012-3456 " * 2000)[:100_000]

    started = time.perf_counter()
    scan(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"scan took {elapsed:.2f}s — check for a backtracking pattern"


def test_unicode_does_not_crash_or_shift_offsets():
    text = "clé API: AKIAIOSFODNN7EXAMPLE — 日本語 — émoji 🎉"
    finding = next(f for f in scan(text) if f.type == "AWS_ACCESS_KEY_ID")

    assert text[finding.start : finding.end] == "AKIAIOSFODNN7EXAMPLE"
