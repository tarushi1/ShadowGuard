"""
Tests for Stage 1b — the entropy detector.
================================================================

The negative cases here matter more than usual. Entropy is a
BROAD-RECALL, LOW-PRECISION signal: it will happily flag base64
images, minified JavaScript, git SHAs and UUIDs, all of which are
random by design and harmless by nature.

Everything below either proves it catches unknown-format secrets, or
pins down one specific class of false positive so it cannot come back.
"""

import pytest

from backend.app.detectors.entropy_detector import (
    GENERAL_THRESHOLD,
    HEX_THRESHOLD,
    MIN_TOKEN_LENGTH,
    character_classes,
    scan,
    shannon_entropy,
)


def flagged(text: str) -> list[str]:
    return [f.value for f in scan(text)]


# ---------------------------------------------------------------
# The maths
# ---------------------------------------------------------------

def test_entropy_of_a_single_repeated_character_is_zero():
    """You are never surprised by the next character."""
    assert shannon_entropy("aaaaaaaa") == 0.0


def test_entropy_of_an_empty_string_is_zero():
    assert shannon_entropy("") == 0.0


def test_uniform_distribution_reaches_log2_of_the_alphabet():
    """Four symbols used equally carries exactly 2 bits per character."""
    assert shannon_entropy("abcd" * 10) == pytest.approx(2.0)


def test_entropy_rises_with_randomness():
    assert shannon_entropy("aaaabbbb") < shannon_entropy("abcdefgh")


def test_english_scores_higher_than_random_hex():
    """The measurement that drove this module's design.

    Character-level entropy does NOT separate English from random
    strings at this scale — letters rarely repeat inside twenty
    characters. Anyone thresholding on raw entropy alone flags prose
    and misses hex keys. This test exists to keep that fact visible.
    """
    english = shannon_entropy("thequickbrownfoxjump")
    random_hex = shannon_entropy("a3f5c9e17b2d48f6a0c3e5b7d9f1a2c4")

    assert english > random_hex


def test_character_classes_is_what_actually_discriminates():
    assert character_classes("thequickbrownfoxjump") == 1        # English
    assert character_classes("user_authentication_token") == 1   # identifier
    assert character_classes("a3f5c9e17b2d48f6a0c3e5b") == 2     # hex
    assert character_classes("aB3xK9mQ2wR7tY5uI8oP1sD4") == 3    # random


# ---------------------------------------------------------------
# POSITIVE — the point of the stage
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "secret",
    [
        "aB3xK9mQ2wR7tY5uI8oP1sD4fG6h",
        "Xk92mNvQ8wRt5YuI3oPa7SdF1gHj4KlZbC6nM0pQ",
        "a3f5c9e17b2d48f6a0c3e5b7d9f1a2c4",
    ],
)
def test_detects_random_looking_tokens(secret):
    """None of these match any regex rule.

    That is the whole justification for this stage: a token from an
    internal service, or a vendor whose format we have never seen,
    still looks random.
    """
    assert secret in flagged(f"the token is {secret} use it")


def test_finds_a_secret_no_regex_rule_knows_about():
    text = "internal auth: Zq7Wm2Kp9Xr4Nv6Bt8Lc3Hd5Fj1Gy0Ae"
    assert len(scan(text)) == 1


def test_entropy_findings_never_exceed_medium_severity():
    """A random-looking string is SUSPICIOUS, not proven.

    This stage must be able to warn and must never block on its own —
    base64 images and minified JS would take the whole tool down with
    it. The cap is the honest expression of what entropy can know.
    """
    findings = scan("token aB3xK9mQ2wR7tY5uI8oP1sD4fG6h here")

    assert findings
    assert all(f.severity == "medium" for f in findings)


def test_confidence_never_reaches_certainty():
    """The detector cannot know WHAT it found, only that it looks
    random. Claiming 1.0 would have the risk engine weight it like a
    private-key header match, which it is nothing like."""
    findings = scan("token Xk92mNvQ8wRt5YuI3oPa7SdF1gHj4KlZbC6nM0pQ here")

    assert findings
    assert all(0.5 <= f.score <= 0.85 for f in findings)


# ---------------------------------------------------------------
# NEGATIVE — every one of these would be a false positive
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "how do I sort a list of dictionaries in python",
        "thequickbrownfoxjumpsoverthelazydog",
        "user_authentication_token_handler_factory",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "please review the pull request when you get a chance",
        "MyPasswordIsSecretHere",
        "",
    ],
)
def test_ordinary_text_and_identifiers_are_not_flagged(text):
    assert scan(text) == []


def test_uuids_are_not_flagged():
    """Random by design, public by nature, and pasted constantly."""
    assert scan("request id 550e8400-e29b-41d4-a716-446655440000") == []


def test_git_commit_shas_are_not_flagged():
    """Forty lowercase hex characters is almost always a commit SHA —
    an identifier that reveals nothing and appears in every code
    conversation."""
    assert scan("see commit e83c5163316f89bfbde7d9ab23ca2e25604af290") == []


def test_short_tokens_are_ignored_entirely():
    """Below the minimum length an entropy estimate is not meaningful:
    there are too few characters for the distribution to say anything."""
    short = "aB3xK9mQ2wR7"
    assert len(short) < MIN_TOKEN_LENGTH
    assert scan(f"value {short} here") == []


def test_urls_do_not_form_one_long_token():
    """The token charset excludes '.' and ':' precisely so that URLs,
    paths and version strings break into short fragments rather than
    fusing into a single high-entropy blob."""
    assert scan("see https://example.com/docs/getting-started/installation") == []


# ---------------------------------------------------------------
# Charset-aware thresholds
# ---------------------------------------------------------------

def test_hex_uses_a_lower_threshold_than_base64():
    """Hex is capped at 4.0 bits by its 16-symbol alphabet; base64 can
    reach 6.0. A single threshold would either miss every hex secret
    or flood on base64."""
    assert HEX_THRESHOLD < GENERAL_THRESHOLD
    assert HEX_THRESHOLD < 4.0


def test_a_hex_secret_below_the_general_threshold_is_still_caught():
    token = "a3f5c9e17b2d48f6a0c3e5b7d9f1a2c4"

    assert shannon_entropy(token) < GENERAL_THRESHOLD   # a single threshold would miss it
    assert token in flagged(f"key {token}")


# ---------------------------------------------------------------
# Offsets
# ---------------------------------------------------------------

def test_offsets_point_at_the_actual_token():
    token = "aB3xK9mQ2wR7tY5uI8oP1sD4fG6h"
    text = f"the value is {token} okay"

    finding = scan(text)[0]

    assert text[finding.start : finding.end] == token


# ---------------------------------------------------------------
# Known-safe classes — found by the evaluation harness
# ---------------------------------------------------------------
# Each of these was a real false positive on the labelled corpus. The
# fixes are rules about CLASSES of identifier, not exclusions of the
# particular strings that failed — that distinction is what separates
# a principled improvement from overfitting to the test set.

def test_content_digests_are_not_secrets():
    """A hash of public content is public by construction."""
    assert scan("npm reports integrity sha512-Kd8Fj3Nq7Wt2Yr5BvXz9Qm4Lp1Hs6Tg0Ue3") == []
    assert scan("image digest sha256:9f2c1a44b7e3d8506a1c4f9b2e7d3a8c5f0b6e1d4a9c2f7b3") == []


def test_vendor_test_keys_are_not_secrets():
    """The regex stage already ignores these deliberately.

    Entropy flagging the same string was an inconsistency in our own
    design rather than a threshold to tune.
    """
    assert scan("use sk_test_51H8xKlMnOpQrStUvWxYz1234 in the staging environment") == []
    assert scan("the publishable key is pk_test_51H8xKlMnOpQrStUvWxYz1234") == []


def test_file_paths_do_not_fuse_into_a_token():
    """Excluding '/' from the token charset.

    Trade-off: standard base64 uses '/', so this costs some recall on
    raw encoded blobs. The secrets that matter here use URL-safe
    alphabets, and encoded blobs are out of scope for Stage 1.
    """
    assert scan("the s3 object key is uploads/2026/07/29/a7b3c9d1e5f2/report.pdf") == []


def test_the_exclusions_do_not_disable_real_detection():
    """The rules above must narrow the detector, not neuter it.

    The risk with any exclusion list is that it quietly grows until
    nothing is detected. A `sha512-` prefix is safe; the same random
    body without it is not.
    """
    body = "Kd8Fj3Nq7Wt2Yr5BvXz9Qm4Lp1Hs6Tg0Ue3"

    assert scan(f"integrity sha512-{body}") == []
    assert body in flagged(f"the signing key is {body}")
