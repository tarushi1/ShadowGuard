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


# ---------------------------------------------------------------
# Context requirements
# ---------------------------------------------------------------

def test_the_same_token_is_sensitive_or_not_depending_on_context():
    """The clearest single demonstration of what this project claims.

    Identical characters, opposite verdicts, decided entirely by the
    surrounding words. One is a tax identifier being USED; the other is
    a format being DISCUSSED. No pattern, checksum or entropy measure
    can separate them, because the evidence is not in the token.
    """
    used = "the vendor submitted PAN ABCDE1234F for gst registration"
    discussed = "our sku format is ABCDE1234F which collides with the validator"

    assert "INDIA_PAN" in types_in(used)
    assert "INDIA_PAN" not in types_in(discussed)


def test_twelve_digits_without_supporting_words_are_not_an_aadhaar():
    """The weakest pattern in the file needs the strongest gate.

    Twelve digits is also a badge number, an order id and a phone
    range. Before this rule was gated, the sample below was reported as
    critical national identity data.
    """
    assert types_in("employee badge numbers run from 100000000000 to 999999999999") == set()


def test_context_words_are_matched_whole():
    """`pan` inside `company`, `expand` or `Japan` must not count.

    Without word boundaries the gate would be satisfied by most English
    prose and would quietly stop doing anything — the worst kind of
    failure, because every test above it would still pass.
    """
    assert "INDIA_PAN" not in types_in("the company issued ABCDE1234F as a part number")


def test_a_supporting_word_beyond_the_window_does_not_count():
    """Proximity is the point.

    "Does the word appear anywhere in the message?" is the tempting
    simplification. In a long message it lets `aadhaar` in the first
    sentence validate a twelve-digit build number in the ninth.
    """
    from backend.app.detectors.regex_detector import _CONTEXT_WINDOW

    far = "aadhaar" + ("x" * (_CONTEXT_WINDOW + 20)) + " 2345 6789 0123"
    assert "INDIA_AADHAAR" not in types_in(far)


def test_context_may_follow_the_match():
    """People write the number first at least as often as last."""
    assert "INDIA_AADHAAR" in types_in("2345 6789 0123 is the aadhaar on the form")


def test_a_match_cannot_supply_its_own_context():
    """A security property, not a tidiness one.

    If the searched window included the match, a PAN whose letters
    happen to spell a context word would validate itself — a gate the
    sender can satisfy by choosing a convenient value is not a gate.
    """
    from backend.app.detectors.regex_detector import has_supporting_context

    text = "PANAB1234C"
    assert not has_supporting_context(text, 0, len(text), "INDIA_PAN")


def test_strong_rules_are_not_gated():
    """Context requirements are for weak patterns only.

    A vendor prefix, a checksum or a unique header is already
    sufficient evidence. Demanding context from those would trade away
    recall for nothing.
    """
    assert types_in("AKIAIOSFODNN7EXAMPLE") == {"AWS_ACCESS_KEY_ID"}
    assert types_in("4111 1111 1111 1111") == {"CREDIT_CARD"}


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

def test_rules_sharing_a_name_agree_on_severity():
    """Two rules MAY report the same entity type deliberately.

    `PASSWORD` is matched by two rules because the separator changes
    how much evidence the label carries: an explicit `password: X`
    tolerates a weaker value test than a bare `password X`. Merging
    them into the same reported type is the intent, not an accident.

    What must not differ is what the merge MEANS. Two rules with one
    name and two severities would make the verdict depend on which
    happened to match first, which is the bug the original
    distinct-names check was really guarding against.
    """
    from collections import defaultdict

    by_name = defaultdict(set)
    for rule in RULES:
        by_name[rule.name].add(rule.severity)

    for name, severities in by_name.items():
        assert len(severities) == 1, f"{name} is reported at {severities}"


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


# ---------------------------------------------------------------
# Credentials — the gap a real user found
# ---------------------------------------------------------------

def test_a_password_beside_its_label_is_detected():
    """The gap that prompted this rule.

    A user pasted a real password and watched it pass every stage.
    Nothing in this file could have caught it: a password has NO
    FORMAT, so no pattern matches, and a short lowercase-and-digit
    string is below the entropy stage's 20-character minimum and would
    fail its threshold anyway.
    """
    for text in (
        "my password is Tr0ub4dorFake",
        "this is my gmail password - Hunter2Fake",
        "login: admin  pwd: letmein123",
        "DB_PASSWORD: sup3rSecretFake",
        "set MYSQL_PWD=abc123xyz in the env",
    ):
        assert "PASSWORD" in types_in(text), f"missed a credential in: {text}"


def test_environment_variable_names_match_despite_the_underscore():
    """`\\b` never fires after an underscore, because `_` is a word
    character — so `MYSQL_PWD=` would not match a `\\b`-anchored rule.
    Pasted config is exactly where credentials show up."""
    assert "PASSWORD" in types_in("export DB_PASSWD=hunter2fake")


def test_sentences_about_passwords_are_not_disclosures():
    """A rule that fires on complaints about passwords is a rule people
    switch off, taking the real detections with it — the argument in
    B8, applied to credentials."""
    for text in (
        "my password is too short apparently",
        "the password is wrong, can you help",
        "your password is expired, reset it",
        "how should we hash passwords, argon2id or bcrypt",
        "set your password to something memorable",
    ):
        assert "PASSWORD" not in types_in(text), f"false positive on: {text}"


def test_masked_and_placeholder_passwords_are_ignored():
    """Flagging a placeholder trains people to dismiss the warning —
    D17's rule applied to credentials."""
    for text in ("password: ********", "password = <yourpassword>", "pwd: changeme"):
        assert "PASSWORD" not in types_in(text), f"flagged a placeholder in: {text}"


def test_hyphenated_words_are_not_read_as_disclosures():
    """`-` is a separator only with spaces around it, so
    `password-protected` stays one ordinary word."""
    assert "PASSWORD" not in types_in("this is a password-protected document")
    assert "PASSWORD" not in types_in("we need to bypass: the cache")


def test_credentials_in_a_connection_string_are_critical():
    """Unambiguous, unlike a labelled password: a colon-separated
    secret before an @ in a URI is what a connection string IS."""
    found = scan("db is postgres://appuser:s3cretFake@db.internal:5432/prod")
    types = {f.type for f in found}

    assert "CONNECTION_STRING_PASSWORD" in types
    assert next(f for f in found if f.type == "CONNECTION_STRING_PASSWORD").severity == "critical"


def test_only_the_secret_is_reported_not_the_label():
    """`group=1` matters for redaction. Reporting the whole match would
    redact to `[REDACTED: PASSWORD]` and lose the sentence; reporting
    the group gives `password is [REDACTED: PASSWORD]`, which still
    reads as something someone can answer."""
    text = "my password is Tr0ub4dorFake"
    finding = next(f for f in scan(text) if f.type == "PASSWORD")

    assert finding.value == "Tr0ub4dorFake"
    assert "password" not in text[finding.start:finding.end].lower()


def test_a_password_is_high_not_critical():
    """Every critical rule here is anchored to something unforgeable —
    a vendor prefix, a PEM header, a checksum. This one rests on
    English word order, and blocking on weak evidence is how a control
    loses the authority to block on strong evidence."""
    finding = next(f for f in scan("my password is Tr0ub4dorFake") if f.type == "PASSWORD")

    assert finding.severity == "high"
    assert finding.score < 0.9, "confidence should reflect that the evidence is word order"


# ---------------------------------------------------------------
# Credential phrasing — generality, not just the examples I wrote
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "my password is Hunter2Fake",
        "password Hunter2Fake",                     # no separator at all
        "here is the password Hunter2Fake",
        "user=admin password=Hunter2Fake",
        "DB_PASSWORD: sup3rSecretFake",
        "set MYSQL_PWD=abc123xyz in the env",
        "the auth token is abc123xyz",
        "auth token: Kj8sLm2p",
        "my token is 7fXq2Lm9",
        "the api key is Zz44kdMe",
        "secret: Tr0ub4dorFake",
        "creds are admin / Hunter2Fake",
        "credentials admin:Hunter2Fake",
        "passcode 44Kd9Lm2",
    ],
)
def test_credentials_are_caught_across_natural_phrasings(text):
    """The generality test this rule did not have.

    The first version recognised five label words and REQUIRED an
    explicit separator. Against fifteen phrasings a person might
    actually type, it caught TWO — `auth token is ...`, `secret: ...`,
    `api key is ...` and even `password Hunter2Fake` all passed
    straight through.

    That was found by a user writing normally, not by any test here,
    which is the same lesson as D46: a corpus scores the detectors you
    built against the samples you thought to write. These cases exist
    so the rule cannot silently narrow again.
    """
    assert "PASSWORD" in types_in(text), f"missed a credential in: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "my password manager keeps logging me out",
        "explain what a JWT token is and how it works",
        "the token economy is an interesting concept",
        "pass the test data through the parser",
        "what is the key insight from this paper",
        "the secret to good code is small functions",
        "store the api key safely in a react native app",
        "how do I rotate credentials without downtime",
        "the access token expires after one hour",
        "what does the auth token contain",
        "the s3 object key is uploads/2026/07/29/a7b3c9d1e5f2/report.pdf",
        "the meeting bridge is 0000 0000 0000 0000 and the pin is four digits",
    ],
)
def test_broadening_the_labels_did_not_cost_precision(text):
    """The other half, and the half that constrains the first.

    Broadening the label list to catch real phrasings immediately cost
    two corpus false positives — `key` matched an S3 path and `pin`
    matched the word "four". Both words were removed from the weak set
    rather than patched around, because no stopword list can enumerate
    every English word that might follow them.
    """
    assert "PASSWORD" not in types_in(text), f"false positive on: {text}"
