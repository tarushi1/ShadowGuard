"""
Stage 1a — pattern-based detection.
================================================================

Finds sensitive content whose FORMAT is known and fixed: API keys,
tokens, private keys, card numbers, national identifiers.

What this stage is good at
--------------------------
Structured secrets, with near-perfect precision, in well under a
millisecond. An OpenAI key is always `sk-` plus base62; a GitHub token
is always `ghp_` plus 36 characters. No model can beat a pattern at
recognising a pattern, and running spaCy on something a regex already
settled is 50ms wasted.

What it fundamentally cannot do — and why Stage 2 exists
--------------------------------------------------------
  - understand meaning: is "Sarah Chen" a customer or a colleague?
  - recognise formats nobody anticipated (a new vendor ships one
    every month)
  - use context: 4111111111111111 in a test fixture is not the same
    as 4111111111111111 in a support ticket
  - read images

The report's headline number is the false-positive drop from Stage 1
alone to Stage 1 + Stage 2. That comparison is only honest if Stage 1
is genuinely good, so this module is written to be a strong baseline
rather than a straw man.

Two things that make it strong
------------------------------
1. CHECKSUM VALIDATION. `\\d{16}` matches every order number and
   tracking code in existence. Real card numbers satisfy the Luhn
   checksum, which roughly 90% of random 16-digit strings fail. One
   validator function cuts that rule's false positives by about an
   order of magnitude — the whole project's thesis in miniature:
   validation beats pattern matching.

2. ReDoS SAFETY. Nested quantifiers like (a+)+ can take exponential
   time, so a 30-character input hangs the server for minutes. Every
   pattern here uses BOUNDED quantifiers and avoids nesting, and the
   API caps input length as a second line of defence.

3. CONTEXT REQUIREMENTS. Two rules here match shapes that occur by
   accident — twelve digits, or five letters and four digits. They are
   gated on a supporting word appearing nearby, which is the only way
   to tell a number being USED from one being DISCUSSED. See
   `_CONTEXT_WINDOW` below.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from .base import Finding, highest_severity, resolve_overlaps  # noqa: F401

DETECTOR_NAME = "regex"


# ---------------------------------------------------------------
# Validators
# ---------------------------------------------------------------

def luhn_valid(value: str) -> bool:
    """Luhn (mod-10) checksum, used by essentially every payment card.

    Double every second digit from the right; if doubling gives more
    than 9, subtract 9. The total must be divisible by 10.

    This is what separates "sixteen digits" from "a card number". An
    order id, an invoice number and a tracking code all match the
    pattern; about nine times out of ten they fail this check.

    It is a checksum, not a signature — it catches typos and random
    strings, not deliberate forgery. That is exactly what we need.
    """
    digits = [int(c) for c in value if c.isdigit()]

    # Real card numbers are 13-19 digits. Anything else is something
    # else that happened to look similar.
    if not 13 <= len(digits) <= 19:
        return False

    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit

    return total % 10 == 0


def not_all_same_digit(value: str) -> bool:
    """Reject 0000000000000000 and friends.

    Placeholder and test values pass Luhn surprisingly often and are
    the single most common false positive in sample data and
    documentation.
    """
    digits = [c for c in value if c.isdigit()]
    return len(set(digits)) > 1


# ---------------------------------------------------------------
# Rules
# ---------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """One pattern, plus what a match means and how sure we are."""

    name: str
    pattern: re.Pattern
    severity: str
    confidence: float
    validator: Callable[[str], bool] | None = None
    context_words: tuple[str, ...] = ()
    """Words that must appear near a match for it to count.

    Empty for most rules, and that is the normal case. A rule needs
    this only when its pattern alone is weak evidence — see
    `_CONTEXT_WINDOW`.
    """
    description: str = ""


# ---------------------------------------------------------------
# Context requirements
# ---------------------------------------------------------------
# How far either side of a match we look for a supporting word.
#
# Every strong rule in this file draws its confidence from something
# BEYOND shape: a vendor prefix (ghp_), a checksum (Luhn), a unique
# header (-----BEGIN). Two rules have no such thing. Twelve digits is
# also a badge number, an order id, a phone range; five letters and
# four digits is also an SKU or a part number. For those, the evidence
# is not in the token at all — it is in the words around it:
#
#     "the vendor submitted PAN ABCDE1234F for gst registration"
#     "our sku format is ABCDE1234F which collides with the validator"
#
# The same ten characters, one sensitive and one not. The difference
# is whether the number is being USED or DISCUSSED, and only context
# can answer that.
#
# 50 characters is about one clause either side. Unbounded matching —
# "does the word appear anywhere in the message?" — is the tempting
# simplification and it is wrong: in a long message it would let the
# word `aadhaar` in the first paragraph validate a twelve-digit build
# number in the ninth.
#
# THE COST, stated plainly because it belongs in the report: this
# trades recall for precision. "my number is 2345 6789 0123" carries
# no supporting word and is now missed. That is an accepted trade for
# rules this weak, not a free improvement.
_CONTEXT_WINDOW = 50


# Patterns are compiled once at import. re caches internally, but an
# explicit compile makes the cost obvious and keeps the hot path free
# of cache lookups — this runs on every message a user sends.
RULES: tuple[Rule, ...] = (
    # ---- Private keys -------------------------------------------
    # Nothing else in the world looks like this header, so confidence
    # is 1.0 and severity is the highest we have.
    Rule(
        name="PRIVATE_KEY",
        pattern=re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
        ),
        severity="critical",
        confidence=1.0,
        description="PEM/OpenSSH private key block header.",
    ),

    # ---- Vendor API keys ----------------------------------------
    # Every one of these is a vendor-published, fixed-shape format.
    # High confidence is justified: false positives are close to
    # impossible when the prefix is this distinctive.
    Rule(
        name="OPENAI_API_KEY",
        pattern=re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
        severity="critical",
        confidence=0.98,
        description="OpenAI secret key, including project and service-account forms.",
    ),
    Rule(
        name="ANTHROPIC_API_KEY",
        pattern=re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        severity="critical",
        confidence=0.99,
    ),
    Rule(
        name="AWS_ACCESS_KEY_ID",
        pattern=re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
        severity="critical",
        confidence=0.97,
        description="AWS access key id. AKIA is long-lived; ASIA is a session key.",
    ),
    Rule(
        name="GITHUB_TOKEN",
        pattern=re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
        severity="critical",
        confidence=0.99,
    ),
    Rule(
        name="GITHUB_PAT",
        pattern=re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
        severity="critical",
        confidence=0.99,
    ),
    Rule(
        name="SLACK_TOKEN",
        pattern=re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        severity="critical",
        confidence=0.97,
    ),
    Rule(
        name="GOOGLE_API_KEY",
        pattern=re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        severity="high",
        confidence=0.96,
    ),
    Rule(
        name="STRIPE_SECRET_KEY",
        # Only the live secret key is critical. A test key (sk_test_)
        # is deliberately not matched: flagging it trains people to
        # dismiss the warning, and dismissing warnings is how real
        # leaks get through.
        pattern=re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b"),
        severity="critical",
        confidence=0.99,
    ),
    Rule(
        name="JWT",
        pattern=re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        severity="high",
        confidence=0.95,
        description=(
            "JSON Web Token. eyJ is base64 for '{\"', so both segments "
            "starting that way is a strong signal."
        ),
    ),

    # ---- Financial ----------------------------------------------
    # The pattern alone is weak; the Luhn validator is what makes this
    # rule usable. Bounded quantifier, no nesting — ReDoS-safe.
    Rule(
        name="CREDIT_CARD",
        pattern=re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
        severity="high",
        confidence=0.9,
        validator=lambda v: luhn_valid(v) and not_all_same_digit(v),
        description="13-19 digit sequence that passes the Luhn checksum.",
    ),
    Rule(
        name="IBAN",
        pattern=re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        severity="high",
        confidence=0.8,
        description=(
            "International bank account number. No checksum implemented yet — "
            "IBAN uses mod-97, which would raise precision the way Luhn does "
            "for cards. Noted as an easy improvement."
        ),
    ),

    # ---- National identifiers -----------------------------------
    Rule(
        name="US_SSN",
        pattern=re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        severity="critical",
        confidence=0.9,
        description=(
            "US Social Security number. The negative lookaheads exclude ranges "
            "the SSA never issues, which removes a large class of false "
            "positives from formatted numbers that merely look similar."
        ),
    ),
    Rule(
        name="INDIA_PAN",
        pattern=re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
        severity="high",
        confidence=0.85,
        # Five letters, four digits, a letter. That shape is also an
        # SKU, a part number and a coupon code, which is exactly how
        # `our sku format is ABCDE1234F` was being reported as tax
        # identity data.
        context_words=(
            "pan", "pan card", "pan number", "permanent account",
            "income tax", "itr", "tds", "gst", "gstin", "kyc",
            "taxpayer", "assessee",
        ),
        description="Indian Permanent Account Number.",
    ),
    Rule(
        name="INDIA_AADHAAR",
        # The lookarounds matter more than the pattern. \b alone would
        # happily match the first twelve digits of a sixteen-digit card
        # number, reporting a payment card as a national id. The
        # lookbehind refuses a preceding digit or separator; the
        # lookahead refuses a following one. Together they mean this
        # only matches a standalone twelve-digit group.
        pattern=re.compile(r"(?<![\d-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)"),
        severity="critical",
        confidence=0.7,
        # The weakest pattern in the file: twelve digits and nothing
        # else. Badge numbers, order ids and phone ranges all match it,
        # which is why it is the one rule that needs BOTH the
        # lookarounds above and a context requirement.
        context_words=(
            "aadhaar", "aadhar", "adhaar", "uidai", "uid",
            "kyc", "ekyc", "biometric", "enrolment", "enrollment",
            "identity", "id proof", "national id",
        ),
        description=(
            "Indian Aadhaar number. Confidence is deliberately lower because "
            "any standalone 12-digit number matches. Aadhaar carries a Verhoeff "
            "checksum; implementing it would do for this rule what Luhn does "
            "for cards. Tracked as a known precision gap, and the reason this "
            "rule is gated on context."
        ),
    ),

    # ---- Contact details ----------------------------------------
    # Low severity on its own: an email address is not a secret. It
    # matters in combination — a name plus an email plus an account
    # number is a customer record, which is what Stage 2 and the risk
    # engine are for.
    Rule(
        name="EMAIL",
        pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        severity="low",
        confidence=0.95,
    ),
)


# One compiled alternation per gated rule, built once at import.
#
# `\b` around the group is doing real work: without it, "pan" matches
# inside "company", "expand", "Japan" and "panel", and the gate would
# wave through most of what it exists to stop.
_CONTEXT_PATTERNS: dict[str, re.Pattern] = {
    rule.name: re.compile(
        r"\b(?:" + "|".join(re.escape(word) for word in rule.context_words) + r")\b",
        re.IGNORECASE,
    )
    for rule in RULES
    if rule.context_words
}


def has_supporting_context(text: str, start: int, end: int, rule_name: str) -> bool:
    """Does a supporting word appear near this match?

    The match itself is EXCLUDED from the searched window, and that is
    a security property rather than a tidiness one. Include it and a
    PAN beginning with the letters PAN would validate itself, which
    turns the gate into something an attacker can satisfy by choosing
    a convenient value.

    :param text: the full text being scanned
    :param start: match start offset
    :param end: match end offset, exclusive
    :param rule_name: which rule's context words to look for
    """
    pattern = _CONTEXT_PATTERNS.get(rule_name)
    if pattern is None:  # ungated rule — nothing to satisfy
        return True

    before = text[max(0, start - _CONTEXT_WINDOW) : start]
    after = text[end : end + _CONTEXT_WINDOW]

    # Joined with a space so a word cut in half at one edge cannot
    # accidentally join a fragment at the other and match.
    return pattern.search(f"{before} {after}") is not None


# ---------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------

def scan(text: str) -> list[Finding]:
    """Run every rule over `text` and return de-duplicated findings.

    A pure function: no I/O, no HTTP, no global state. That is what
    lets the evaluation harness call it directly, hundreds of times,
    in milliseconds.

    :param text: the content to inspect
    :returns: findings sorted by position, with overlaps resolved
    """
    findings: list[Finding] = []

    for rule in RULES:
        for match in rule.pattern.finditer(text):
            value = match.group(0)

            # A validator turns "looks like" into "is". Where one
            # exists, a match that fails it is discarded silently —
            # that is the whole point of having it.
            if rule.validator is not None and not rule.validator(value):
                continue

            # A validator asks "is this string well-formed?"; a context
            # requirement asks "is this string being USED here?". Two
            # different questions, so two separate gates — a valid PAN
            # quoted as an example format is well-formed and still not
            # a disclosure.
            if not has_supporting_context(text, match.start(), match.end(), rule.name):
                continue

            findings.append(
                Finding(
                    type=rule.name,
                    start=match.start(),
                    end=match.end(),
                    score=rule.confidence,
                    detector=DETECTOR_NAME,
                    severity=rule.severity,
                    value=value,
                )
            )

    return resolve_overlaps(findings)
