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

from .base import Finding, context_window, highest_severity, resolve_overlaps  # noqa: F401

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
# Credential validators
# ---------------------------------------------------------------
# Words that follow "password is" in a sentence ABOUT passwords rather
# than one containing a password.
#
# Without this, "my password is too short", "the password is wrong" and
# "your password is expired" all report a credential disclosure — and a
# rule that fires on complaints about passwords is a rule people switch
# off, taking the real detections with it (the argument in B8).
_PASSWORD_STOPWORDS = frozenset({
    "too", "very", "not", "the", "a", "an", "in", "on", "of", "to", "and",
    "wrong", "incorrect", "invalid", "expired", "correct", "right", "fine",
    "short", "long", "weak", "strong", "safe", "secure", "insecure",
    "required", "optional", "case", "sensitive", "blank", "empty", "same",
    "different", "stored", "hashed", "encrypted", "reset", "changed",
    "here", "there", "it", "this", "that", "what", "why", "how", "when",
})

# Obvious stand-ins. Flagging a placeholder trains people to dismiss
# the warning, which is D17's rule applied to credentials.
_PASSWORD_PLACEHOLDERS = frozenset({
    "password", "passwd", "pwd", "yourpassword", "mypassword", "changeme",
    "xxxxxx", "xxxxxxxx", "placeholder", "example", "redacted", "hidden",
    "null", "none", "todo", "tbd",
})


# Credential labels, longest first so the alternation prefers
# "api key" over "key" and "auth token" over "token".
#
# STRONG labels are words that mean a credential and little else, so a
# bare space is enough of a separator: "password Hunter2Fake".
_LABELS_STRONG = (
    "passphrase", "passcode", "password", "passwd",
    "credentials", "creds",
    "client secret", "secret key", "api secret",
    "auth token", "access token", "bearer token", "api token", "auth key",
    "api key", "apikey", "api_key",
)

# WEAK labels are ordinary English words that also name credentials.
# They require an EXPLICIT separator — "token: abc" counts, "token abc"
# does not — because "the token economy" and "pass the test" are
# ordinary prose.
#
# `key` and `pin` were here and are deliberately NOT. Broadening the
# rule with them cost two corpus false positives immediately:
#
#   "the s3 object key is uploads/2026/07/29/..."  -> flagged the PATH
#   "...and the pin is four digits"                -> flagged "four"
#
# Both words are too common in technical prose to carry evidence on
# their own, and no stopword list can enumerate every English word that
# might follow them. Nothing real is lost: `api key`, `auth key` and
# `secret key` are all STRONG labels already, so the credential senses
# of "key" are still covered while the ordinary sense is not.
#
# This is the precision/recall trade of D29 in miniature — the two
# labels that would have caught the most also cost the most.
_LABELS_WEAK = ("token", "secret", "pwd", "pass")


# Words that mark a digit run as a telephone number.
#
# Deliberately excludes the generic word "number", which precedes
# account numbers, order numbers, reference numbers and invoice
# numbers far more often than it precedes a telephone.
# Bare "call", "text", "contact" and "reach" were here and are
# deliberately NOT. Each is far commoner in technical prose than in a
# telephone context -- "which API should we call", "the text field",
# "the contact page" -- and "call" alone cost a corpus regression
# immediately: it gated a phone match onto an AADHAAR number in
#
#   "aadhaar 8901 2345 6789, what verification api should we call"
#
# PHONE then outranked INDIA_AADHAAR on confidence and, being `low`
# severity, downgraded a national identifier to an allow.
#
# The verb forms are kept as PHRASES instead, which is the distinction
# that actually matters: "call me on" is a telephone, "call the API" is
# not.
_PHONE_CONTEXT = (
    "phone", "telephone", "mobile", "cell", "cellphone", "tel",
    "dial", "sms", "whatsapp", "helpline", "landline",
    "call me", "call him", "call her", "call them", "call us",
    "ring me", "ring him", "ring her", "reach me", "reach him", "reach her",
    "contact number", "contact me", "contact him", "contact her",
)


def _alternation(labels: tuple[str, ...]) -> str:
    return "|".join(re.escape(word).replace(r"\ ", r"\s+") for word in labels)


def looks_credential_shaped(value: str) -> bool:
    """Does this token look generated rather than typed?

    The guard that makes a bare-space separator safe. Without it,
    "password manager keeps logging me out" reports `manager` as the
    credential.

    A real credential almost always contains a digit or mixes case;
    an ordinary English word following a label does neither. This is
    the same test the OCR repair uses to decide whether a fragment is
    part of a split secret or the next word of a sentence.
    """
    cleaned = value.strip("\"'`.,;!?")

    return any(c.isdigit() for c in cleaned) or (
        any(c.isupper() for c in cleaned) and any(c.islower() for c in cleaned)
    )


def looks_like_a_real_password(value: str) -> bool:
    """Is this an actual credential, or a sentence about one?

    A password has no format — that is the whole difficulty. `sparrow419`
    is indistinguishable from a username, a filename or a variable name,
    and no pattern can separate them. The only evidence is the LABEL
    beside it, which the rule matches, plus these two negative checks.

    So this rule is weaker than every other one in the file, and its
    confidence says so.
    """
    cleaned = value.strip().strip("\"'`.,;!?")

    if len(cleaned) < 4:
        return False

    # Masked in the message already: ****, ••••, <password>, [redacted]
    if set(cleaned) <= set("*•.xX-_") or cleaned.startswith(("<", "[", "{")):
        return False

    lowered = cleaned.lower()

    return lowered not in _PASSWORD_STOPWORDS and lowered not in _PASSWORD_PLACEHOLDERS


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

    group: int = 0
    """Which capture group holds the sensitive span.

    0 (the whole match) for almost every rule. Credential rules need
    1, because their pattern has to match the LABEL to find the secret
    — `password is hunter2` — while only `hunter2` is the secret.

    It matters for redaction: with group 0 the output is
    `[REDACTED: PASSWORD]` and the sentence loses its subject; with
    group 1 it is `password is [REDACTED: PASSWORD]`, which still reads
    as a question someone can answer.
    """

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

    # ---- Credentials --------------------------------------------
    # The gap a user found by pasting a real password and watching it
    # sail through every stage.
    #
    # Nothing else in this file could have caught it. A password has NO
    # FORMAT — `sparrow419` is shaped exactly like a username, a filename
    # or a variable name — so no pattern matches it, and at ten
    # lowercase-and-digit characters it is below the entropy stage's
    # 20-character minimum and would fail its threshold anyway.
    #
    # The only evidence is the label beside it. That makes this the
    # purest application of D29's principle in the file: the rule
    # matches the LABEL in order to locate the secret, and reports only
    # the secret via `group=1`.
    #
    # `high` rather than `critical`, deliberately. Every critical rule
    # here is anchored to something unforgeable — a vendor prefix, a PEM
    # header, a checksum. This one rests on English word order, which is
    # weaker evidence, and blocking on weak evidence is how a control
    # loses the authority to block on strong evidence.
    # Two rules, one entity type, because the SEPARATOR changes how much
    # evidence the label carries.
    #
    # The first version required an explicit separator and recognised
    # only five label words. Tested against fifteen natural phrasings it
    # caught TWO -- `auth token is ...`, `secret: ...`, `api key is ...`
    # and `password Hunter2Fake` all sailed through. That is
    # brittleness, not detection, and it was found by a user writing
    # normally rather than by any test in this repository.
    Rule(
        name="PASSWORD",
        pattern=re.compile(
            # `(?<![A-Za-z0-9])` rather than `\b`, so `MYSQL_PWD=` and
            # `DB_PASSWORD:` match — an underscore is a word character,
            # so `\b` never fires after one, and environment variables
            # are where credentials most often appear in a pasted config.
            # It still refuses `compass:` and `bypass:`, because the
            # character before is alphanumeric there.
            r"(?<![A-Za-z0-9])(?:" + _alternation(_LABELS_STRONG + _LABELS_WEAK) + r")"
            # Separators. `=` and `:` need no spaces; the word and dash
            # forms REQUIRE them, so `password-protected` and
            # `password-based` are not read as disclosures.
            r"(?:\s*[=:]\s*|\s+(?:is|was|are|-|–|—|->)\s+)"
            r"(?:the\s+)?"
            r"[\"'`]?([^\s\"'`,;]{4,64})",
            re.IGNORECASE,
        ),
        severity="high",
        confidence=0.75,
        group=1,
        validator=looks_like_a_real_password,
        description=(
            "A credential disclosed beside its label, with an explicit "
            "separator. Confidence is the lowest in the file because the "
            "evidence is word order rather than format."
        ),
    ),
    Rule(
        name="PASSWORD",
        # No separator at all: "password Hunter2Fake", "here is the
        # password Hunter2Fake".
        #
        # STRONG labels only, and the value must LOOK generated. Both
        # restrictions are load-bearing: with weak labels this matches
        # "pass the test", and without the shape check it reports
        # `manager` from "password manager keeps logging me out".
        pattern=re.compile(
            r"(?<![A-Za-z0-9])(?:" + _alternation(_LABELS_STRONG) + r")"
            r"\s+(?:for\s+|to\s+)?[\"'`]?([^\s\"'`,;]{5,64})",
            re.IGNORECASE,
        ),
        severity="high",
        confidence=0.7,
        group=1,
        validator=lambda v: looks_like_a_real_password(v) and looks_credential_shaped(v),
        description="A credential adjacent to its label, with no separator.",
    ),
    Rule(
        name="CONNECTION_STRING_PASSWORD",
        # scheme://user:PASSWORD@host — unambiguous, unlike the rule
        # above. A colon-separated secret before an @ in a URI is what
        # a connection string IS, so this one is critical.
        pattern=re.compile(
            r"\b[a-z][a-z0-9+.\-]{1,15}://[^\s:@/]{1,64}:([^\s@/]{1,128})@",
            re.IGNORECASE,
        ),
        severity="critical",
        confidence=0.95,
        group=1,
        validator=looks_like_a_real_password,
        description="Credentials embedded in a database or service URI.",
    ),

    # ---- Telephone numbers --------------------------------------
    # Split into two rules because the FORMAT carries different
    # amounts of evidence.
    #
    # A leading "+" country code, or a parenthesised area code, or a
    # 3-3-4 hyphenated group, is a telephone number and essentially
    # nothing else. A bare run of ten digits is also an order number,
    # an invoice id, a tracking code and a timestamp -- so that form is
    # gated on a nearby word, exactly as INDIA_AADHAAR is (D29).
    #
    # Severity is LOW, in the `contact` class alongside EMAIL, and that
    # is the important design decision rather than the patterns. A
    # switchboard number on a company website is not sensitive; the
    # same number beside a customer's name is a contactable identity.
    # Expressing that is the risk engine's job, not this file's -- so
    # a phone number alone stays `allow` and only combines into
    # something actionable (D33).
    Rule(
        name="PHONE",
        pattern=re.compile(
            # +91 98765 43210 · +1 (555) 123-4567 · +44 20 7946 0958
            # The trailing group is OPTIONAL: "+91 98765 43210" has two
            # groups after the country code, "+1 555 123 4567" has three.
            r"\+\d{1,3}[-.\s]?\(?\d{2,5}\)?[-.\s]?\d{3,5}(?:[-.\s]?\d{3,5})?\b"
            # (555) 123-4567
            r"|\(\d{3}\)\s?\d{3}[-.\s]\d{4}\b"
            # 555-123-4567 — three groups, distinct from the 3-2-4 of a
            # US SSN, so the two rules cannot collide.
            r"|\b\d{3}-\d{3}-\d{4}\b",
        ),
        severity="low",
        confidence=0.85,
        description="Telephone number in a self-evident format.",
    ),
    Rule(
        name="PHONE",
        # The ambiguous forms: a bare national number, or loosely
        # grouped digits. The trailing lookahead stops this matching
        # the first twelve digits of a payment card.
        pattern=re.compile(
            r"(?<![\d-])(?:"
            r"[6-9]\d{9}"                       # Indian mobile
            r"|0\d{2,4}[-.\s]\d{3,4}[-.\s]?\d{3,4}"  # 020 7946 0958
            # Loose triplet, with one shape carved out of it: FOUR-FOUR-FOUR.
            #
            # Twelve digits in three groups of four is the printed form
            # of an Aadhaar number, and it is not the form of any
            # telephone number anywhere -- a subscriber number that long
            # carries a country code, which the branch above already
            # handles. Left in, this branch claimed
            #
            #   "their aadhaar is 2345 6789 0123 ... phone +91 ..."
            #
            # as a PHONE, and at 0.8 against Aadhaar's 0.7 it won the
            # overlap, turning a `critical` national identifier into a
            # `low` contact detail.
            #
            # Note what is NOT being changed to fix that. The overlap
            # resolver ranks confidence first, on purpose: severity says
            # how bad a finding is IF TRUE, confidence says how likely
            # it is TO BE TRUE, and choosing between two readings of the
            # same characters is a question about truth. The resolver
            # was right; the claim fed to it was false. The second time
            # this collision has appeared (see _PHONE_CONTEXT above),
            # and the first fix -- deleting "call" from the vocabulary
            # -- treated the symptom, which is why it came back through
            # another word. Denying the shape closes it for every word.
            r"|(?!\d{4}[-.\s]\d{4}[-.\s]\d{4}(?![-.\s]?\d))"
            r"\d{3,4}[-.\s]\d{3,4}[-.\s]\d{3,4}"
            r")(?![-.\s]?\d)"
        ),
        severity="low",
        confidence=0.8,
        context_words=_PHONE_CONTEXT,
        description=(
            "Telephone number in an ambiguous format. Gated on a nearby "
            "word because ten digits is equally an order or invoice number."
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
# Keyed by rule IDENTITY, not by name.
#
# Two rules may deliberately share a name — PHONE and PASSWORD are each
# split in two, because the FORMAT of a match changes how much evidence
# it carries. Keying this by name made the pair share one gate: the
# self-evident `(555) 123-4567` rule silently inherited the context
# requirement belonging to the ambiguous bare-digits rule, and was
# rejected for having no nearby "phone".
#
# `id()` is stable here because RULES is a module-level tuple that lives
# for the process lifetime.
_CONTEXT_PATTERNS: dict[int, re.Pattern] = {
    id(rule): re.compile(
        r"\b(?:" + "|".join(re.escape(word) for word in rule.context_words) + r")\b",
        re.IGNORECASE,
    )
    for rule in RULES
    if rule.context_words
}


# ---------------------------------------------------------------
# Conversation priming
# ---------------------------------------------------------------
# The closed vocabulary that may be carried between messages.
#
# CLOSED is the load-bearing word. `core/conversation.py` remembers
# which of THESE words a message mentioned and nothing else, so free
# text can never enter that store — there is no code path that looks up
# anything outside these lists. That is what lets a stateful feature sit
# inside a system that promises to retain no message content.
_VOCABULARY = tuple(
    sorted(
        {word for rule in RULES for word in rule.context_words}
        | set(_LABELS_STRONG)
        | set(_LABELS_WEAK)
    )
)

_VOCABULARY_PATTERN = re.compile(
    r"\b(?:" + _alternation(_VOCABULARY) + r")\b", re.IGNORECASE
)

# Labels that specifically announce a CREDENTIAL, as opposed to any
# context word. Priming on `phone` should not make a bare token look
# like a password.
_CREDENTIAL_VOCABULARY = frozenset(_LABELS_STRONG + _LABELS_WEAK)

# A primed message must be SHORT to be read as a bare credential.
#
# The pattern being modelled is the follow-up message that is just the
# secret: "hunter2Fake", or "here it is: hunter2Fake". A paragraph is
# something else, and treating every credential-shaped token inside one
# as a password would turn a narrow fix into a false-positive machine.
PRIMED_CREDENTIAL_MAX_TOKENS = 8

# Lower than either in-message PASSWORD rule (0.75 and 0.7).
#
# The evidence is genuinely weaker: the label and the value were never
# seen together, and the link between them is an assumption about how
# the conversation flows. Scoring it the same would claim a certainty
# that is not there.
PRIMED_CREDENTIAL_CONFIDENCE = 0.6


def context_vocabulary_in(text: str) -> frozenset[str]:
    """Which known context words this message mentioned.

    The only thing `core/conversation.py` is ever given. Returns
    normalised lowercase forms so `API Key` and `api  key` become the
    same entry a rule's `context_words` can be compared against.
    """
    return frozenset(
        re.sub(r"\s+", " ", match.group(0).lower())
        for match in _VOCABULARY_PATTERN.finditer(text)
    )


# Tokens that are obviously something else, measured rather than guessed.
#
# The first version of the bare-credential rule reused
# `looks_credential_shaped`, which accepts a digit OR mixed case. On ten
# ordinary follow-up messages it produced TWO false alarms — `Chrome`
# from "try Chrome 141 instead" and `report2024.pdf` from "the file is
# report2024.pdf". A 20% false-alarm rate on benign text would have made
# the feature worse than the gap it closes, since a warning people learn
# to dismiss protects nothing.
_FILENAME_LIKE = re.compile(r"\.[A-Za-z]{1,5}$")
_URLISH = re.compile(r"^[a-z]+://|^www\.|/", re.IGNORECASE)


def _looks_like_a_bare_credential(value: str) -> bool:
    """A stricter shape test than `looks_credential_shaped`.

    Stricter because it has less to work with. The in-message rules see
    a LABEL beside the value, so a weak shape test is enough — the label
    is carrying the claim. Here the label is a message away, so the
    shape has to carry it alone.

    Both a letter AND a digit, rather than either. That single change
    removes `Chrome`, `Firefox`, `Windows` and every other capitalised
    ordinary word, at the cost of all-alphabetic passwords — which
    `looks_like_a_real_password` was already unable to distinguish from
    prose anyway.
    """
    # A range, not a floor. Below 6 it is a word; above 32 it is not
    # something a human typed — it is a git SHA, an npm integrity hash,
    # a docker digest or a trace id, and every one of those appeared in
    # the benign corpus. Long generated tokens already belong to the
    # ENTROPY stage, which runs on every message and is far better at
    # judging them. Claiming them here would be a second detector
    # answering a question that already has an owner.
    if not 6 <= len(value) <= 32:
        return False

    # Syntax characters. `r=t||0` came out of a line of minified
    # JavaScript in the benign corpus; assignment and grouping
    # characters mark code, and a typed password does not contain them.
    if any(c in value for c in "=|(){}<>"):
        return False

    if _FILENAME_LIKE.search(value) or _URLISH.search(value):
        return False

    # No dots or slashes at all. Versions (`python3.12`), hostnames and
    # paths all carry them and passwords rarely do, so the trade is
    # cheap. `python3.12 is installed` was a false alarm without this.
    if any(c in value for c in "./\\"):
        return False

    # A LOWERCASE letter and a digit, not merely a letter and a digit.
    # Reference codes are the reason: `ENG-4471`, `INV-2024`, `PR-1183`
    # are letter-plus-digit and are not credentials. Requiring lowercase
    # removes that whole family, and the cost — an all-uppercase
    # password sent bare with no label — is a narrow case to give up in
    # exchange for not crying wolf on every ticket number.
    return any(c.isdigit() for c in value) and any(c.islower() for c in value)


def _scan_primed_credentials(text: str, primed: frozenset[str]) -> list[Finding]:
    """Read a short message as a bare credential, when one was announced.

    This is the case the whole feature exists for:

        "I'm going to paste my password"
        "hunter2Fake"

    The PASSWORD rules embed the label in their pattern, so neither can
    fire on the second message — there is no label to match. Satisfying
    a context gate does not help either, because the gate is not what is
    missing. The only thing that works is a rule that looks for a bare
    credential, and that rule is far too broad to run unconditionally:
    without the announcement it would report a token from every short
    message anybody sends.

    Three bounds keep it narrow: the conversation must have mentioned a
    CREDENTIAL label specifically, the message must be short, and the
    token must still look generated rather than typed.
    """
    if not (primed & _CREDENTIAL_VOCABULARY):
        return []

    if len(text.split()) > PRIMED_CREDENTIAL_MAX_TOKENS:
        return []

    findings: list[Finding] = []

    # Maximal runs, deliberately UNBOUNDED here, with the length test
    # applied afterwards in `_looks_like_a_bare_credential`.
    #
    # A capped quantifier looked equivalent and was not: a 71-character
    # docker digest exceeded the cap, matched its first 64 characters,
    # and left a 7-character tail that was then judged on its own merits
    # and reported as a password. Splitting a token you are about to
    # reject for being too long is how a length limit becomes a way
    # around itself.
    for match in re.finditer(r"""[^\s"'`,;]+""", text):
        value = match.group(0)

        if not looks_like_a_real_password(value):
            continue
        if not _looks_like_a_bare_credential(value):
            continue

        findings.append(
            Finding(
                type="PASSWORD",
                start=match.start(),
                end=match.end(),
                score=PRIMED_CREDENTIAL_CONFIDENCE,
                detector=DETECTOR_NAME,
                severity="high",
                value=value,
            )
        )

    return findings


def has_supporting_context(
    text: str,
    start: int,
    end: int,
    rule: "Rule",
    primed: frozenset[str] = frozenset(),
) -> bool:
    """Does a supporting word appear near this match?

    The match itself is EXCLUDED from the searched window, and that is
    a security property rather than a tidiness one. Include it and a
    PAN beginning with the letters PAN would validate itself, which
    turns the gate into something an attacker can satisfy by choosing
    a convenient value.

    :param text: the full text being scanned
    :param start: match start offset
    :param end: match end offset, exclusive
    :param rule: the rule being applied; its own context words are used
    """
    pattern = _CONTEXT_PATTERNS.get(id(rule))
    if pattern is None:  # ungated rule — nothing to satisfy
        return True

    if pattern.search(context_window(text, start, end, _CONTEXT_WINDOW)) is not None:
        return True

    # The supporting word may have arrived in an EARLIER message.
    #
    # "here is my aadhaar" followed by a bare twelve-digit number is one
    # disclosure typed across two lines, and the gate exists to
    # distinguish a real disclosure from a coincidental digit run — a
    # question the previous message answers perfectly well.
    #
    # `primed` is empty on every path except a live conversation, so the
    # evaluation harness and every existing caller are unaffected.
    return bool(primed & set(rule.context_words))


# ---------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------

def scan(text: str, primed: frozenset[str] = frozenset()) -> list[Finding]:
    """Run every rule over `text` and return de-duplicated findings.

    Still a pure function: `primed` is passed in rather than looked up,
    so this performs no I/O and holds no state of its own. The default
    is empty, which means the evaluation harness and every existing
    caller behave exactly as before — the corpus numbers stay a claim
    about single-message detection.

    :param text: the content to inspect
    :param primed: context words carried from earlier messages in the
        same conversation, drawn only from the closed vocabulary above
    :returns: findings sorted by position, with overlaps resolved
    """
    findings: list[Finding] = []

    for rule in RULES:
        for match in rule.pattern.finditer(text):
            # `group` lets a rule match a label to LOCATE its secret
            # while reporting only the secret itself.
            value = match.group(rule.group)
            if value is None:
                continue

            start, end = match.span(rule.group)

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
            if not has_supporting_context(text, start, end, rule, primed):
                continue

            findings.append(
                Finding(
                    type=rule.name,
                    start=start,
                    end=end,
                    score=rule.confidence,
                    detector=DETECTOR_NAME,
                    severity=rule.severity,
                    value=value,
                )
            )

    findings.extend(_scan_primed_credentials(text, primed))

    return resolve_overlaps(findings)
