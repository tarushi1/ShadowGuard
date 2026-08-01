"""
The risk engine — weighted fusion over findings.
================================================================

Replaces the Week 3 placeholder, which took the single worst severity
and used it as the verdict.

Why the placeholder had to go, in one measurement
-------------------------------------------------
On the 200-sample corpus it allowed 11 sensitive messages through, and
**8 of those 11 had completely correct detection**. Every entity the
ground truth asked for was found; the message went through anyway,
because `PERSON` and `ORGANIZATION` are `low` severity and `low` maps
to `allow`.

A max-severity policy can only ever answer "what is the worst single
thing here?". It cannot express:

    a name + an employer + a working email address is a customer
    record, even though no one of those three is sensitive

That sentence is the entire justification for this module.

What it does NOT do — and the trap it exists to avoid
-----------------------------------------------------
The obvious implementation is "warn when there are two or more
findings". Measured on this corpus, that catches 8 of the 11 misses and
costs **13 benign false positives**, including:

    explain how Satya Nadella restructured Microsoft
    compare the leadership styles of Jacinda Ardern and Angela Merkel
    write a story about Alice and Bob exchanging encrypted messages

Counting findings is not fusion. Those messages contain exactly the
same entity types as the ones that should be stopped. What separates
them is whether the named person is a DATA SUBJECT of the sender's
organisation or a TOPIC being discussed — the same used-vs-discussed
distinction that D29 applied to Aadhaar, one level up.

So the score is built from three things, not one:

  1. a weighted, repetition-damped sum over findings
  2. bonuses where classes COMBINE into something neither is alone
  3. amplification where surrounding words mark a person as a subject

All of the numbers live in `policy.py`. This module is the arithmetic.
"""

import re
from dataclasses import dataclass

from ..detectors.base import Finding, context_window
from ..schemas.analyze import Action, RiskLevel
from .policy import CLASS_OF, DATA_SUBJECT_MARKERS, DEFAULT_POLICY, RiskPolicy

# `\b` matters as much here as it did in D29: without it, `hr` matches
# inside "through" and "shrink", and every message becomes an HR record.
_MARKER_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in DATA_SUBJECT_MARKERS) + r")\b",
    re.IGNORECASE,
)

# Classes whose weight the data-subject context can amplify.
#
# ONLY `identity`, and the reason is definitional rather than tuned:
# a data subject is a natural person. Organisations and places are not
# data subjects, under GDPR or under any sensible reading, so a marker
# word near an ORGANIZATION says nothing about personal data.
#
# Including `affiliation` here was the first version and it produced
# exactly the false positive that mistake predicts:
#
#     "our test fixtures all use Foo Barrington as the customer name"
#
# ORGANIZATION beside the marker `customer`, amplified to 27, warned.
# No person is present, so no amplification should have happened.
#
# Deliberately not `secret` or `financial` either. An API key is
# exactly as dangerous in a bare sentence as in a rich one, and letting
# context raise a secret's score invites the mirror-image mistake of
# letting context lower it.
_AMPLIFIABLE = frozenset({"identity"})

# What each combination MEANS, for the person reading the modal.
#
# Written as consequences rather than as rule names. "identity +
# contact matched" tells a user nothing; "a name together with a way to
# contact them" tells them what we think their message contains, which
# is the difference between a warning they can act on and one they
# learn to click through.
_COMBINATION_REASONS: dict[tuple[str, str], str] = {
    ("contact", "identity"): "a name together with a way to contact them",
    ("government_id", "identity"): "a name together with a government identifier",
    ("financial", "identity"): "a name together with financial account details",
    ("affiliation", "identity"): "a named person tied to an organisation or place",
}


@dataclass(frozen=True)
class RiskAssessment:
    """The engine's verdict, with its reasoning attached.

    `reasons` exists because a risk score nobody can explain is a risk
    score nobody will trust. When a user asks why a message was
    stopped, "score 61" is not an answer; "a person's name, their
    employer and a contactable address" is. It is also what makes the
    Week 7 dashboard able to show WHY rather than only WHAT.
    """

    score: int
    action: Action
    level: RiskLevel
    reasons: tuple[str, ...]


def assess(
    findings: list[Finding],
    text: str,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> RiskAssessment:
    """Fuse findings into a single verdict.

    :param findings: everything the detection stages produced
    :param text: the analysed text, needed for context lookups
    :param policy: the tunable opinions; defaults to DEFAULT_POLICY
    """
    if not findings:
        return RiskAssessment(0, Action.ALLOW, RiskLevel.LOW, ())

    reasons: list[str] = []
    score = _base_score(findings, text, policy, reasons)
    score += _combination_bonus(findings, policy, reasons)

    # Clamped, not normalised. A message containing four private keys
    # is not four times worse than one containing a single key — past
    # a point the question is already settled and further evidence
    # changes nothing.
    score = max(0.0, min(100.0, score))

    return RiskAssessment(
        score=round(score),
        action=_action_for(score, policy),
        level=_level_for(score, policy),
        reasons=tuple(reasons),
    )


def _base_score(
    findings: list[Finding],
    text: str,
    policy: RiskPolicy,
    reasons: list[str],
) -> float:
    """Weighted sum over findings, with repeats damped."""
    seen: dict[str, int] = {}
    total = 0.0

    # Strongest first, so the repetition decay penalises the WEAKEST
    # duplicate rather than an arbitrary one. Processing in document
    # order would let a low-confidence match claim the undamped slot
    # and push a better one into the decayed tail.
    for finding in sorted(findings, key=lambda f: -f.score):
        weight = policy.severity_weights.get(finding.severity, 0.0)

        # Confidence scales the weight because severity says how bad
        # this would be IF TRUE and confidence says how likely it is to
        # be true. Multiplying is how the two combine into an expected
        # cost; ranking on one and ignoring the other is the bug D16
        # was written about.
        contribution = weight * finding.score

        occurrence = seen.get(finding.type, 0)
        contribution *= policy.repetition_decay**occurrence
        seen[finding.type] = occurrence + 1

        if _has_data_subject_context(finding, text, policy):
            contribution *= policy.data_subject_multiplier
            if occurrence == 0:
                reasons.append(
                    f"{_label(finding.type)} named as a data subject, not as a topic"
                )
        elif occurrence == 0:
            reasons.append(_label(finding.type))

        total += contribution

    return total


def _has_data_subject_context(finding: Finding, text: str, policy: RiskPolicy) -> bool:
    """Is this person or organisation a subject of a record?

    The distinction the max-severity policy could not make, and the
    reason 8 correctly-detected messages were allowed through.
    """
    if CLASS_OF.get(finding.type) not in _AMPLIFIABLE:
        return False

    window = context_window(text, finding.start, finding.end, policy.context_radius)

    return _MARKER_PATTERN.search(window) is not None


def _combination_bonus(
    findings: list[Finding],
    policy: RiskPolicy,
    reasons: list[str],
) -> float:
    """Reward classes that mean more together than apart.

    Applied once per class PAIR, not once per finding pair. Three
    email addresses beside one name is still one linkable identity, and
    scoring it as three would make the bonus a second, sneakier way of
    counting.
    """
    present = {CLASS_OF.get(f.type) for f in findings}
    present.discard(None)

    total = 0.0
    for pair, bonus in policy.combination_bonuses.items():
        if pair[0] in present and pair[1] in present:
            total += bonus

            # Worth a sentence a human can read. These are the lines a
            # user sees when they ask why their message was stopped, so
            # each says what the combination MEANS rather than naming
            # the rule that fired.
            reasons.append(_COMBINATION_REASONS[pair])

    return total


def _action_for(score: float, policy: RiskPolicy) -> Action:
    if score >= policy.block_threshold:
        return Action.BLOCK

    # Redaction stays unreachable until 5.3 implements it. Emitting an
    # action the extension cannot perform would leave a message neither
    # sent nor blocked, silently.
    if policy.redact_threshold is not None and score >= policy.redact_threshold:
        return Action.REDACT

    if score >= policy.warn_threshold:
        return Action.WARN

    return Action.ALLOW


def _level_for(score: float, policy: RiskPolicy) -> RiskLevel:
    for floor, level in policy.level_bands:
        if score >= floor:
            return RiskLevel(level)

    return RiskLevel.LOW


def _label(entity_type: str) -> str:
    return entity_type.replace("_", " ").lower()
