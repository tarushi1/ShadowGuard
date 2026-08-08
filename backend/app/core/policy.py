"""
Risk policy — the tunable half of the risk engine.
================================================================

Every number an organisation might reasonably want to change lives
here, and nothing else does. `risk.py` holds the arithmetic; this file
holds the opinions.

Why the split matters
---------------------
"Block on `critical`" is not a fact about detection, it is a choice
about consequence. A bank and a startup can run identical detectors and
still disagree about whether a payment card should stop a message or
merely warn about it. Encoding that choice inside the scoring code
would make it a code change, reviewable only by whoever understands
Python, when it should be a policy change reviewable by whoever owns
the risk.

It also makes the evaluation harness able to COMPARE policies, which is
the point: "these thresholds produce this recall and this false-positive
rate" is a far better report than "we picked 25 and it seemed fine".

Everything below is a `RiskPolicy` field, so an alternative policy is
`replace(DEFAULT_POLICY, warn_threshold=40)` — one line, no code
touched.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------
# Combination rules are written over CLASSES, not entity types.
#
# "A person plus a way to contact them is a linkable identity" is a
# statement about categories. Writing it as PERSON+EMAIL, then again
# for PERSON+PHONE when a phone rule is added, then again for
# PERSON+HANDLE, produces a rule set that grows quadratically and
# quietly stops covering the newest detector.

CLASS_OF: dict[str, str] = {
    # Who someone is.
    "PERSON": "identity",
    # How to reach them. Alone: harmless. Attached to a name: a
    # linkable, contactable individual, which is personal data under
    # GDPR in a way that neither half is on its own.
    "EMAIL": "contact",
    # Issued by a state, uniquely identifying, and not reissuable.
    "US_SSN": "government_id",
    "INDIA_AADHAAR": "government_id",
    "INDIA_PAN": "government_id",
    # Money movement.
    "CREDIT_CARD": "financial",
    "IBAN": "financial",
    # Grants access to a system rather than describing a person.
    #
    # Passwords sit here rather than in a class of their own: what
    # matters for fusion is that they GRANT ACCESS, which is the same
    # thing an API key does. The difference between them is how
    # confidently we detected one, and that is already carried by
    # `score` — see D46 on why PASSWORD is `high` and not `critical`.
    "PASSWORD": "secret",
    "CONNECTION_STRING_PASSWORD": "secret",
    "PRIVATE_KEY": "secret",
    "OPENAI_API_KEY": "secret",
    "ANTHROPIC_API_KEY": "secret",
    "AWS_ACCESS_KEY_ID": "secret",
    "GITHUB_TOKEN": "secret",
    "GITHUB_PAT": "secret",
    "SLACK_TOKEN": "secret",
    "GOOGLE_API_KEY": "secret",
    "STRIPE_SECRET_KEY": "secret",
    "JWT": "secret",
    "HIGH_ENTROPY_STRING": "secret",
    # Context about a person rather than an identifier of one.
    "ORGANIZATION": "affiliation",
    "LOCATION": "affiliation",
    "NRP": "affiliation",
}


# ---------------------------------------------------------------
# Data-subject markers
# ---------------------------------------------------------------
# Words that signal a named person is a SUBJECT OF A RECORD held by
# the sender's organisation, rather than a topic being discussed.
#
# This is 4.2's distinction (D29) one level up. There it separated a
# number being USED from one being DISCUSSED; here it separates a
# person who is our data subject from a person who is our subject
# matter:
#
#     "our client Deborah Alvarez is threatening to sue"     -> subject
#     "explain how Satya Nadella restructured Microsoft"     -> topic
#
# Both contain a PERSON and an ORGANIZATION. Nothing about the entities
# separates them; only the surrounding relationship language does.
#
# ON THE RISK OF OVERFITTING, stated plainly because it is real:
# these words were chosen from the domain — the vocabulary of HR,
# support, clinical and compliance records — and the list is
# deliberately broader than the corpus needs, so that breadth is
# evidence rather than assertion. But the corpus samples were written
# by the same author as this list, and no amount of care removes that.
# The honest position is that the measured benefit here is OPTIMISTIC,
# and that validating this rule requires prompts this project did not
# write. That belongs in the report's threats-to-validity section, not
# in a footnote. See D34.
DATA_SUBJECT_MARKERS: tuple[str, ...] = (
    # Relationship to the organisation
    "client", "customer", "patient", "employee", "employees", "staff",
    "candidate", "applicant", "contractor", "vendor", "supplier",
    "tenant", "policyholder", "claimant", "beneficiary", "subscriber",
    "member", "student", "resident", "complainant", "whistleblower",
    # Record and process types
    "performance review", "appraisal", "termination", "redundancy",
    "disciplinary", "grievance", "background check", "onboarding",
    "offboarding", "payroll", "medical notes", "medical record",
    "diagnosis", "prescription", "case file", "case notes", "kyc",
    "complaint", "claim", "dispute", "escalation", "ticket",
    "shortlist", "roster", "personnel", "hr",
)


@dataclass(frozen=True)
class RiskPolicy:
    """One complete set of scoring opinions."""

    # ---- Severity weights ---------------------------------------
    # The score a single maximally-confident finding of each severity
    # contributes, out of 100.
    #
    # `low` is 8 rather than 20 deliberately. Low-severity findings are
    # common in perfectly ordinary messages — 35 of the 100 benign
    # corpus samples contain at least one — so a weight high enough for
    # two of them to warn would fire on `explain how Satya Nadella
    # restructured Microsoft`. Low findings earn their keep through the
    # combination and context rules below, not through their base
    # weight.
    #
    # `medium` is 50 rather than 35, and the number is DERIVED rather
    # than chosen. D19 states the design intent for the entropy stage:
    # it must be able to warn, and must never block. Entropy confidence
    # runs from 0.50 to 0.85 across the corpus, so:
    #
    #     weight * 0.50 >= warn_threshold  (25)  ->  weight >= 50
    #     weight * 0.85 <  block_threshold (90)  ->  weight <  105
    #
    # The first version used 35, which put the warn line at confidence
    # 0.71 — straight through the middle of the entropy distribution,
    # so roughly half of all entropy findings silently stopped warning.
    # It cost two true positives that the previous max-severity policy
    # had caught. The lesson generalises: a weight that is not derived
    # from a stated intent will cut some distribution in half, and you
    # will not know which one until it costs you something.
    severity_weights: dict[str, float] = field(
        default_factory=lambda: {
            "critical": 95.0,
            "high": 60.0,
            "medium": 50.0,
            "low": 8.0,
        }
    )

    # ---- Repetition decay ---------------------------------------
    # The nth finding of the same type contributes weight * decay^(n-1).
    #
    # Without this, ten email addresses (80 points) outrank one private
    # key (95 becomes reachable by volume), and a message is judged by
    # how many times a detector fired rather than by what it found. Ten
    # addresses in a mailing list are one fact, not ten.
    repetition_decay: float = 0.5

    # ---- Combination bonuses ------------------------------------
    # Added once when BOTH classes are present. Keyed by a sorted pair.
    #
    # This is the "two weak signals together are serious" case that a
    # max-severity policy structurally cannot express, and it is the
    # reason this engine exists. `identity` + `contact` is the
    # important one: a name is not personal data in any useful sense, an
    # email address alone identifies nobody in particular, and the two
    # together are a specific, contactable human being.
    combination_bonuses: dict[tuple[str, str], float] = field(
        default_factory=lambda: {
            ("contact", "identity"): 40.0,
            ("government_id", "identity"): 30.0,
            ("financial", "identity"): 30.0,
            # A named person at a named organisation is weaker than the
            # above — it is how people discuss public figures all day —
            # so the bonus is small and does not warn on its own.
            ("affiliation", "identity"): 6.0,
        }
    )

    # ---- Data-subject context -----------------------------------
    # Multiplier applied to an `identity` or `affiliation` finding when
    # a marker word appears within `context_radius` characters.
    #
    # 4.0 is set so one marked PERSON (8 * 0.85 * 4.0 = 27.2) clears the
    # warn threshold while two unmarked ones (8 * 0.85 + 8 * 0.85 * 0.5
    # = 10.2) do not. Those two numbers are the whole design: the
    # engine must separate `our client Deborah Alvarez` from `compare
    # Jacinda Ardern and Angela Merkel`, and it is the marker that
    # separates them, not the count.
    data_subject_multiplier: float = 4.0
    context_radius: int = 60

    # ---- Thresholds ---------------------------------------------
    # Score at or above which each action is taken.
    warn_threshold: float = 25.0
    block_threshold: float = 90.0

    # At or above this, OFFER TO REMOVE the findings rather than merely
    # warning about them — provided the message survives it.
    #
    # 50 sits just below a Luhn-validated payment card (60 * 0.9 = 54),
    # which is the canonical redactable case: one structured token
    # inside a sentence that still means something without it.
    #
    # Semantic-only messages score 27-46 and therefore stay at `warn`,
    # which falls out of the design rather than being special-cased.
    # That is the right outcome: in "our client Deborah Alvarez is
    # threatening to sue", the name is not an incidental token in the
    # message, it IS the message. Redacting it produces a question
    # nobody can answer.
    redact_threshold: float | None = 50.0

    # Redaction is only offered when at least this much of the message
    # survives it. Someone who pastes a private key and nothing else
    # would otherwise be handed
    #
    #     [REDACTED: PRIVATE_KEY]
    #
    # which is not a message, it is a receipt. Past this line the
    # correct answer is `block`, and this is what keeps `block`
    # meaningful rather than vestigial: it means "there is nothing here
    # except the thing we cannot send".
    max_redacted_fraction: float = 0.5

    # Classes that can be removed without destroying the message.
    #
    # `identity` and `affiliation` are deliberately absent. A secret is
    # INCIDENTAL to the sentence around it — "charge [REDACTED:
    # CREDIT_CARD] for the invoice" still asks a coherent question. A
    # person is usually the SUBJECT of the sentence, and removing them
    # leaves nothing to answer:
    #
    #     "send the signed contract to [REDACTED: PERSON] at
    #      [REDACTED: ORGANIZATION]"
    #
    # That is not a redacted message, it is a destroyed one, and
    # offering it as a remedy teaches users that the redact button
    # produces garbage.
    #
    # Note this does NOT mean personal data is never redacted. Removing
    # the EMAIL from "Miriam Feldstein can be reached at [REDACTED:
    # EMAIL]" breaks the linkability that made the pair sensitive while
    # leaving the question intact — which is exactly the point.
    redactable_classes: frozenset[str] = frozenset(
        {"secret", "financial", "government_id", "contact"}
    )

    # ---- Level bands --------------------------------------------
    # Score -> RiskLevel, for the modal's colour and wording. Separate
    # from actions on purpose: an organisation may warn on `high`
    # rather than block, and that must not require relabelling the
    # severity shown to the user.
    level_bands: tuple[tuple[float, str], ...] = (
        (90.0, "critical"),
        (55.0, "high"),
        (25.0, "medium"),
        (0.0, "low"),
    )


DEFAULT_POLICY = RiskPolicy()
