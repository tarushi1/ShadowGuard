"""
Stage 2 — semantic detection via Microsoft Presidio.
================================================================

Stage 1 asks "does this string look like a secret?". This stage asks
"what do these words MEAN?".

Named Entity Recognition labels spans by what they refer to — PERSON,
ORGANIZATION, LOCATION — using SURROUNDING WORDS rather than the shape
of the token:

    "Sarah Chen approved the refund"      Sarah Chen -> PERSON
    "Chen Dynasty pottery is on display"  Chen Dynasty -> ORG

Same token, different label, decided by context. No regex can do that,
and not because the pattern is hard: the answer is not in the pattern.

Division of labour with Stage 1
-------------------------------
Presidio ships ~30 recognizers, and many duplicate ours — EMAIL_ADDRESS,
CREDIT_CARD, US_SSN, IBAN_CODE. Running both would report one secret
twice and inflate any count-based score.

Rather than detecting everything and de-duplicating afterwards, this
stage is restricted by ALLOWLIST to entity types Stage 1 cannot reach.
Avoiding double-counting by construction is more robust than avoiding
it by cleanup, because a new Presidio release adding a recognizer
cannot silently start colliding with ours.

Our regex rules stay authoritative for structured secrets: they are
faster, more precise, checksum-validated, and we control them.

Cost
----
Measured over the 150-sample corpus: mean 2.8ms, p95 3.9ms — against
Stage 1's 0.014ms, so roughly 200x.

That figure depends on the allowlist. Letting Presidio run all ~30 of
its recognizers costs 30-230ms per message; restricting it to the four
types below is a 10x speed-up on top of the double-counting argument.
The narrow scope is a performance decision as much as a correctness
one, which was not obvious until it was measured.

3ms is invisible to a user who has just pressed Enter. It is not
invisible to a harness running 150 samples, and it is still 200x the
cost of Stage 1 — which is the justification for short-circuiting when
Stage 1 has already found something critical, and for building this
engine once as a lazy singleton rather than per request.
"""

import logging
import re
import threading

from ..core.policy import DATA_SUBJECT_MARKERS
from .base import Finding, context_window

DETECTOR_NAME = "presidio"

logger = logging.getLogger(__name__)

# The spaCy model to load.
#
# sm / md / lg differ mainly in WORD VECTORS, not the NER component:
# published NER F-scores are 0.84 / 0.85 / 0.85. `md` therefore gives
# lg-equivalent entity recognition at 1/14 the size (40MB vs 560MB).
# Changing this line is the whole experiment if recall proves
# insufficient.
SPACY_MODEL = "en_core_web_md"

# Entity types this stage is responsible for.
#
# Deliberately narrow: everything here is something Stage 1
# structurally cannot detect. Presidio's EMAIL_ADDRESS, CREDIT_CARD,
# US_SSN and IBAN_CODE recognizers are excluded because our regex
# rules already cover them faster and with checksum validation.
SUPPORTED_ENTITIES = (
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "NRP",  # nationality, religious or political group
)

# Below this, a Presidio result is noise.
#
# Its weak recognizers self-identify: on "employee badge numbers run
# from 100000000000 to 999999999999" it returns US_BANK_NUMBER at 0.05
# and US_DRIVER_LICENSE at 0.01. The allowlist above already excludes
# those, but a threshold means a future allowlist addition cannot
# quietly admit near-zero-confidence guesses.
MIN_SCORE = 0.4

# Severity for semantic findings.
#
# `low` on purpose, and the reasoning matters. A person's name is not a
# secret — people discuss colleagues, authors and public figures all
# day. What makes a name sensitive is COMBINATION: a name plus an
# account number plus an email is a customer record.
#
# Expressing "these three weak signals together are serious" is exactly
# what the Week 5 risk engine is for. Encoding it here would put fusion
# logic inside a detector, where the evaluation harness cannot vary it
# and the report cannot compare it against alternatives.
SEMANTIC_SEVERITY = "low"

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """Create the Presidio engine once, on first use.

    Lazy rather than at import time for two reasons:

      - Loading spaCy costs ~0.7s. Paying that on import would slow
        every pytest run, including the ~120 tests that never touch
        this module.
      - FastAPI would pay it during startup, making a health check
        fail while the process is technically alive.

    Locked because FastAPI runs synchronous handlers in a THREAD POOL,
    so two requests can reach this simultaneously on a cold process
    and would otherwise each build their own engine.

    The explicit NlpEngineProvider configuration is not optional:
    a bare AnalyzerEngine() silently downloads en_core_web_lg (400MB)
    on first use, which presents as an inexplicable multi-minute hang.
    """
    global _engine

    if _engine is not None:
        return _engine

    with _engine_lock:
        if _engine is not None:  # another thread won the race
            return _engine

        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        logger.info("loading spaCy model %s for Stage 2", SPACY_MODEL)

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            }
        )

        _engine = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=["en"],
        )

        logger.info("Stage 2 engine ready")

    return _engine


# How far to look for a marker beside a lowercase candidate.
#
# Mirrors `RiskPolicy.context_radius`, which uses the same vocabulary
# to answer a closely related question. Not read from the policy
# object, because a detector takes no policy — a rule that changes what
# gets DETECTED belongs to the detector, while the policy governs what
# a finding is then WORTH.
NAME_CONTEXT_RADIUS = 60

# The vocabulary is imported rather than restated. It is 89 words and
# it grew by ~45 in one change (D48); a second copy here would be
# correct on the day it was written and wrong within a month.
#
# This is the only place a detector reads from `core`, and it is a
# deliberate exception: `policy.py` is data, not logic, and the
# layering rule it bends exists to keep I/O and HTTP out of detectors,
# not to forbid a shared constant. Purity and testability are
# untouched.
_MARKER_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in DATA_SUBJECT_MARKERS) + r")\b",
    re.IGNORECASE,
)


def _looks_like_a_name(span: str, text: str, start: int, end: int) -> bool:
    """Decide whether a PERSON span is a name or noise.

    On "kyc document shows aadhaar ..." spaCy labels `kyc` as a PERSON
    at 0.85 confidence — high enough that no score threshold helps.
    The model is pattern-matching on sentence position, and short
    lowercase acronyms in that position genuinely look like names to it.

    English proper nouns are capitalised, so requiring one uppercase
    letter removes that entire class in a single rule — a fact about
    the language rather than a tweak fitted to one sample.

    ---- The exception, and why it is safe ------------------------

    The capitalisation rule had a cost that a user found immediately:
    type your own name in lowercase and you stop being a person.
    `customer john, phone 801-555-0142` is a customer record, and the
    filter was throwing away the only signal that said so.

    So a lowercase span is admitted when a DATA-SUBJECT MARKER sits
    beside it — `customer`, `patient`, `claimant`, `employee`. That is
    not a loosening of the rule so much as a second, independent reason
    to believe the span is a name: something in the sentence is
    explicitly framing a person as the subject of a record.

    The obvious objection is that `kyc` is ITSELF one of those markers,
    so this looks like it re-admits the exact bug. It does not, because
    `context_window` EXCLUDES the span being judged. Asked about `kyc`
    in "kyc document shows aadhaar ...", the window is "document shows
    aadhaar ..." and contains no marker, so the span stays rejected.
    Asked about `john` in "kyc for john", the window contains `kyc` and
    the span is admitted. A finding may never supply its own
    justification — the same property D29's context gates depend on.

    Known limitation, still true and worth stating in the report: a
    lowercase name with no marker anywhere near it is still missed, and
    the whole approach assumes a script that has capital letters at
    all. Detection here is English-centric already.
    """
    if len(span.strip()) < 2:
        return False

    if any(c.isupper() for c in span):
        return True

    window = context_window(text, start, end, NAME_CONTEXT_RADIUS)

    if _MARKER_PATTERN.search(window):
        return True

    # ---- The marker swallowed by the span ------------------------
    # spaCy sometimes labels "patient priya" as one PERSON rather than
    # tagging `priya` and leaving `patient` alone. The marker is then
    # INSIDE the span, `context_window` correctly excludes it, and a
    # patient's name beside a phone number scored 6 and was allowed.
    #
    # A span is still not permitted to justify itself, which is what
    # keeps `kyc` rejected: a span that is ONLY a marker contains no
    # name and is refused. But a marker followed by something else
    # means the something else is the name, and that residual — not the
    # marker — is what is being admitted.
    match = _MARKER_PATTERN.search(span)
    if match:
        residual = (span[: match.start()] + span[match.end():]).strip(" ,.:;-")
        return len(residual) >= 2

    return False


def scan(text: str) -> list[Finding]:
    """Find semantic entities Stage 1 cannot reach.

    Not a pure function in the strict sense — it lazily builds a
    process-wide engine — but it performs no I/O per call and is
    deterministic for a given input, which is what the evaluation
    harness needs.

    :param text: content to inspect
    :returns: findings in document order
    """
    if not text.strip():
        return []

    engine = _get_engine()

    results = engine.analyze(
        text=text,
        language="en",
        entities=list(SUPPORTED_ENTITIES),
        score_threshold=MIN_SCORE,
    )

    findings: list[Finding] = []

    for result in results:
        span = text[result.start : result.end]

        if result.entity_type == "PERSON" and not _looks_like_a_name(
            span, text, result.start, result.end
        ):
            continue

        findings.append(
            Finding(
                type=result.entity_type,
                start=result.start,
                end=result.end,
                score=round(result.score, 3),
                detector=DETECTOR_NAME,
                severity=SEMANTIC_SEVERITY,
                value=span,
            )
        )

    return sorted(findings, key=lambda f: f.start)


def warm_up() -> None:
    """Build the engine ahead of the first real request.

    Called at FastAPI startup so the first user to send a message does
    not pay the ~0.7s model load on top of their analysis. Without
    this, exactly one unlucky person per server restart experiences
    ShadowGuard as broken.
    """
    _get_engine()
