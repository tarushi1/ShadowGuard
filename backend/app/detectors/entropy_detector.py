"""
Stage 1b — entropy-based detection.
================================================================

The complement to regex. Where `regex_detector` asks "does this match
a format I anticipated?", this asks "does this look random?" — which
catches secrets whose format nobody has published yet: an internal
service token, a new vendor's key, a generated password.

Neither subsumes the other. Regex has near-perfect precision on known
formats and zero recall on unknown ones; entropy has broad recall and
mediocre precision. Running both is the point.

Why the naive version fails
---------------------------
Every tutorial says "compute Shannon entropy, threshold at 4.5". That
does not work, for two measured reasons.

1. ENTROPY IS CAPPED BY LENGTH. A string of length n can carry at most
   log2(n) bits per character. A 20-character token maxes out at 4.32
   bits no matter how random it is, so a threshold of 4.5 silently
   never fires on short tokens — the failure is invisible.

2. ENGLISH IS HIGH-ENTROPY AT THIS SCALE. Letters rarely repeat inside
   twenty characters. Measured on our own samples:

       thequickbrownfoxjump               H = 4.12   (English!)
       a3f5c9e17b2d48f6a0c3e5b7d9f1a2c4   H = 3.93   (random hex)

   English scores HIGHER than a random hex key. A raw entropy
   threshold flags prose and misses the secret.

What actually discriminates
---------------------------
CHARACTER-CLASS DIVERSITY. Real secrets mix cases and digits; English
words and identifiers do not. From the same measurement:

       random base64        3 classes
       random hex / SHA     2 classes
       English prose        1 class
       snake_case_names     1 class

Class count plus a CHARSET-AWARE entropy threshold separates them
cleanly. Hex has a ceiling of 4.0 bits (16 symbols) so it needs a
lower bar than base64's 6.0; one threshold for both would either miss
every hex secret or flood on base64.

This stage is deliberately weak
-------------------------------
Base64 images, minified JavaScript, git SHAs and UUIDs are all
high-entropy and completely harmless. Findings here are capped at
`medium` severity: an unrecognised random-looking string is
SUSPICIOUS, not proven. It should raise a warning, never a block, on
its own.

That is not a limitation to apologise for — it is the reason the Week
5 risk engine exists. Entropy is a signal to be WEIGHED alongside
others, and claiming more for it than that would be dishonest.
"""

import math
import re
from collections import Counter

from .base import Finding, resolve_overlaps

DETECTOR_NAME = "entropy"

# The one entity type this stage emits, and its severity.
#
# Named constants rather than literals at the construction site because
# `catalog.py` assembles the project-wide entity registry from the
# detectors themselves. A hand-copied table is correct the day it is
# written and wrong the first time a severity changes.
FINDING_TYPE = "HIGH_ENTROPY_STRING"

# Capped at medium on purpose. See the module docstring: random-looking
# is suspicious, not proven, and this stage must never block alone.
MAX_SEVERITY = "medium"

# Below this, an entropy estimate is not meaningful — there are too
# few characters for the distribution to say anything. It also keeps
# ordinary words out of consideration entirely.
MIN_TOKEN_LENGTH = 20

# Thresholds derived from measurement (see the module docstring), not
# from a blog post. Hex is capped at 4.0 bits by its 16-symbol
# alphabet, so it needs a lower bar than base64's 6.0.
HEX_THRESHOLD = 3.5
GENERAL_THRESHOLD = 4.2

# Candidate tokens: runs of characters a secret could plausibly be
# made of. Deliberately excludes '.', ':' and '/' so that URLs, file
# paths and version strings break into short fragments instead of
# forming one long high-entropy token.
#
# Excluding '/' is a trade-off, made after the evaluation harness
# showed an S3 object key being flagged: `uploads/2026/07/29/a7b3c9d1
# e5f2/report` fused into a single token that cleared every threshold.
# Standard base64 does use '/', so this costs some recall on raw
# encoded blobs — but the secrets that matter here (API keys, session
# tokens, signing keys) overwhelmingly use URL-safe alphabets with '-'
# and '_', and encoded blobs are explicitly out of scope for Stage 1.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+=_-]{%d,}" % MIN_TOKEN_LENGTH)

_HEX_PATTERN = re.compile(r"\A[0-9a-fA-F]+\Z")

# ---- Known-safe high-entropy shapes ---------------------------
# Each of these is random by design and public by nature. Flagging
# them would be a pure false positive, and false positives are what
# get a tool switched off.
_UUID_PATTERN = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

# A 40-character lowercase hex string is almost always a git commit
# SHA — an identifier people paste constantly and which reveals
# nothing. Also covers SHA-1 digests.
_GIT_SHA_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")

# Prefixes that declare the token is NOT a secret.
#
# Two families, both principled rather than corpus-specific:
#
#   Content digests. A hash of public content is public by
#   construction — npm integrity strings, docker image digests,
#   checksums. High entropy by design, secret by nobody's definition.
#
#   Vendor test and publishable keys. `sk_test_` and `pk_*` are
#   documented by Stripe as safe to expose. The regex stage already
#   ignores them deliberately; entropy flagging the same string was an
#   inconsistency in our own design, not a tuning question.
#
# The distinction that keeps this from being overfitting: these are
# rules about CLASSES of identifier that generalise beyond the corpus.
# Excluding a literal string that happened to fail would not.
_SAFE_PREFIXES = (
    "sha1-", "sha256-", "sha384-", "sha512-",
    "md5-", "crc32-",
    "sk_test_", "pk_test_", "pk_live_",
)

# The same digest markers, written with a colon — `sha256:9f2c...` in
# docker output. The colon is not in the token charset, so the token
# itself carries no evidence and we have to look at what precedes it.
_DIGEST_MARKERS = ("sha1:", "sha256:", "sha384:", "sha512:", "md5:", "digest:")

# How far back to look for one of those markers. Long enough for
# "sha256:", short enough that an unrelated word several tokens away
# cannot suppress a real finding.
_LOOKBACK = 10


def shannon_entropy(text: str) -> float:
    """Bits of information per character.

    H = -sum over distinct characters of p(c) * log2 p(c)

    Intuition: how surprised you are, on average, by the next
    character. "aaaa" is 0 — you always know what comes next. A
    uniformly random string over k symbols approaches log2(k).
    """
    if not text:
        return 0.0

    length = len(text)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in Counter(text).values()
    )


def character_classes(text: str) -> int:
    """How many of {lowercase, uppercase, digit} appear.

    The discriminator that makes this stage work at all. Measured on
    real samples: random base64 uses all three, hex uses two, English
    prose and snake_case identifiers use one.
    """
    return sum(
        (
            any(c.islower() for c in text),
            any(c.isupper() for c in text),
            any(c.isdigit() for c in text),
        )
    )


def _is_known_safe(token: str, preceding: str = "") -> bool:
    """Random by design, harmless by nature.

    :param token: the candidate string
    :param preceding: the few characters before it, for markers like
                      `sha256:` that the token charset splits away
    """
    if _UUID_PATTERN.match(token) or _GIT_SHA_PATTERN.match(token):
        return True

    lowered = token.lower()
    if lowered.startswith(_SAFE_PREFIXES):
        return True

    return preceding.lower().endswith(_DIGEST_MARKERS)


def _confidence_for(entropy: float, threshold: float, classes: int) -> float:
    """Map how far past the threshold a token sits onto 0.5-0.85.

    Never reaches 1.0, and that ceiling is deliberate. This detector
    cannot know WHAT it found — only that the characters look random.
    A confidence of 1.0 would claim a certainty it does not have, and
    the risk engine would then weight it like a regex match on a
    private-key header, which it is nothing like.
    """
    margin = min((entropy - threshold) / 1.5, 1.0)
    base = 0.5 + 0.25 * margin

    # All three character classes is a meaningfully stronger signal
    # than two, so it earns a small bonus rather than a separate rule.
    if classes >= 3:
        base += 0.1

    return round(min(base, 0.85), 2)


def scan(text: str) -> list[Finding]:
    """Find high-entropy tokens that look like unrecognised secrets.

    A pure function — no I/O, no state — so the evaluation harness can
    call it directly over the corpus.

    :param text: content to inspect
    :returns: findings in document order, overlaps resolved
    """
    findings: list[Finding] = []

    for match in _TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        preceding = text[max(0, match.start() - _LOOKBACK) : match.start()]

        if _is_known_safe(token, preceding):
            continue

        # Single-class tokens are words, identifiers or plain digit
        # runs. Digit runs are the regex stage's business (cards, IDs),
        # and words are not secrets. This one check removes the entire
        # English-prose false-positive class.
        classes = character_classes(token)
        if classes < 2:
            continue

        is_hex = bool(_HEX_PATTERN.match(token))
        threshold = HEX_THRESHOLD if is_hex else GENERAL_THRESHOLD

        entropy = shannon_entropy(token)
        if entropy < threshold:
            continue

        findings.append(
            Finding(
                type=FINDING_TYPE,
                start=match.start(),
                end=match.end(),
                score=_confidence_for(entropy, threshold, classes),
                detector=DETECTOR_NAME,
                severity=MAX_SEVERITY,
                value=token,
            )
        )

    return resolve_overlaps(findings)
