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
from . import redactor
from .behaviour import BehaviourSignal
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
    behaviour: "BehaviourSignal | None" = None,
) -> RiskAssessment:
    """Fuse findings into a single verdict.

    :param findings: everything the detection stages produced
    :param text: the analysed text, needed for context lookups
    :param policy: the tunable opinions; defaults to DEFAULT_POLICY
    :param behaviour: Stage 4's view of how unusual this is for this
        user. OPTIONAL, and defaulting to None is what keeps the
        evaluation harness meaningful: the corpus is a set of isolated
        messages with no user history, so it calls this without a
        signal and measures content detection alone.
    """
    if not findings:
        # Zero times any multiplier is still zero, and that is a
        # property worth stating rather than relying on. Behaviour can
        # raise the priority of a real finding; it must never
        # manufacture a verdict from nothing, or it becomes a second
        # detector that no corpus can measure.
        return RiskAssessment(0, Action.ALLOW, RiskLevel.LOW, ())

    reasons: list[str] = []
    score = _score_of(findings, text, policy, reasons)

    if behaviour is not None and not behaviour.is_neutral:
        score = min(100.0, score * behaviour.multiplier)
        reasons.extend(behaviour.reasons)

    return RiskAssessment(
        score=round(score),
        action=_action_for(score, policy, redactable=_can_redact(findings, text, policy)),
        level=_level_for(score, policy),
        reasons=tuple(reasons),
    )


def _score_of(
    findings: list[Finding],
    text: str,
    policy: RiskPolicy,
    reasons: list[str] | None = None,
) -> float:
    """The full score for a set of findings.

    Called twice: once for everything found, and once for what would
    remain after redaction. Cheap enough to repeat because no detector
    runs here — this is arithmetic over results that already exist.
    """
    if not findings:
        return 0.0

    collected: list[str] = [] if reasons is None else reasons

    score = _base_score(findings, text, policy, collected)
    score += _combination_bonus(findings, policy, collected)

    # Clamped, not normalised. A message containing four private keys
    # is not four times worse than one containing a single key — past
    # a point the question is already settled and further evidence
    # changes nothing.
    return max(0.0, min(100.0, score))


def removable(
    findings: list[Finding],
    policy: RiskPolicy = DEFAULT_POLICY,
) -> list[Finding]:
    """The findings redaction is allowed to strip.

    Public because the orchestrator must redact EXACTLY this set. The
    decision to offer redaction was made by asking what the message
    looks like once these are gone; removing anything else would
    deliver a different message from the one that was approved.

    Getting that wrong produced

        "send the signed contract to [REDACTED: PERSON] at
         [REDACTED: ORGANIZATION], her address is [REDACTED: EMAIL]"

    from a decision that had only ever justified removing the address.
    """
    return [f for f in findings if CLASS_OF.get(f.type) in policy.redactable_classes]


def _can_redact(findings: list[Finding], text: str, policy: RiskPolicy) -> bool:
    """Would removing what we CAN remove actually solve the problem?

    Two conditions, and both matter.

    **Something must be left to say.** Someone who pastes a private key
    and nothing else would otherwise be handed `[REDACTED: PRIVATE_KEY]`,
    which is a receipt rather than a message. That is what `block` is
    for, and this guard is what keeps `block` meaningful.

    **Redaction must RESOLVE the risk, not merely reduce it.** Offering
    to redact and leaving the message still worth warning about is the
    worst of both: the user's text is damaged and they are stopped
    anyway. So the residual score — what remains after removing the
    redactable classes — must fall below the warn threshold.

    This is what correctly separates two cases that look alike:

        "her card 4532015112830366 was double charged"
            remove the card -> a name in an ordinary sentence -> REDACT

        "our client Deborah Alvarez is threatening to sue"
            nothing removable -> still warn-worthy -> WARN
    """
    candidates = removable(findings, policy)
    if not candidates:
        return False

    # Measured over what would ACTUALLY be replaced, not over every
    # finding. Counting spans that redaction leaves untouched inflates
    # the figure and refuses redaction on messages it would have
    # handled perfectly well — which is how
    #
    #     "send the contract to Miriam Feldstein at Halcyon Partners,
    #      her address is m.feldstein@halcyonpartners.co"
    #
    # came out at 70% covered when the only span to be removed was the
    # address, and fell back to a warning it did not need.
    if redactor.coverage(text, candidates) > policy.max_redacted_fraction:
        return False

    remaining = [f for f in findings if f not in candidates]

    return _score_of(remaining, text, policy) < policy.warn_threshold


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


def _action_for(score: float, policy: RiskPolicy, *, redactable: bool) -> Action:
    """Turn a score into an instruction.

    Redaction is checked BEFORE blocking, which is the important
    ordering and deserves the explanation.

    Blocking is a dead end: the user is told no and left to work out
    what to remove, which in practice means they retype the message
    somewhere the tool cannot see it. Offering to strip the secret and
    send the rest is both safer and more useful, so at high scores
    redaction is the preferred remedy and `block` is what happens when
    redaction would not help.

    That is also what stops `block` from becoming vestigial. It now
    means something specific: there is nothing in this message except
    the thing we cannot send.
    """
    if (
        policy.redact_threshold is not None
        and score >= policy.redact_threshold
        and redactable
    ):
        return Action.REDACT

    if score >= policy.block_threshold:
        return Action.BLOCK

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
