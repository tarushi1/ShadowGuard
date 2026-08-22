"""
Stage 4 — behavioural baseline.
================================================================

Stages 1-3 ask "is this message sensitive?". This stage asks a
different question:

    Is this normal FOR THIS PERSON?

A developer who pastes stack traces all day and then pastes an
eight-thousand-character customer export is doing something unusual for
themselves, even when no individual detector fires on it.

WHAT THIS IS NOT
----------------
Not a detector. It produces no findings and names no entities. If it
did, it would pollute the per-entity metrics and the corpus could not
measure it — and worse, it would let "unusual" masquerade as
"sensitive", which are different claims.

It returns a MULTIPLIER on the fused risk score. Detection decides what
is there; this decides how much weight to put on it given who is
sending it.

THE LIMITATION THAT MATTERS MOST, STATED FIRST
----------------------------------------------
`user_id` is CLIENT-SUPPLIED (bypass B6). Anyone can send any value, so
a behavioural profile is reset by changing one string in a request.

That weakness is survivable for the other stages — they inspect content
and do not care who sent it — but it is close to fatal here, because
this stage is *entirely* about identity. A real deployment must take
identity from an authenticated session; until then this stage raises
the cost of a careless mistake and does nothing whatever against a
deliberate one.

It is built anyway because the mechanism is the contribution, and
because the alternative — claiming a behavioural capability without
implementing it — is worse.

COLD START
----------
A new user has no history. A system that reads "no baseline" as
"suspicious" punishes everyone on their first day, and a system that
reads it as "safe" is exploitable by anyone willing to change their id.

This returns a NEUTRAL multiplier until a minimum history exists. That
is the honest answer: with no evidence, add no evidence.
"""

import logging
import statistics
from dataclasses import dataclass, field
from datetime import timedelta

logger = logging.getLogger(__name__)

# Events required before a baseline means anything.
#
# Below this, the standard deviation of a handful of samples is noise,
# and a z-score computed from it would swing wildly on the next
# message. 20 is a compromise: small enough that a real user reaches it
# in a day or two, large enough that one outlier does not define the
# mean.
MIN_HISTORY = 20

# How far back a baseline looks.
#
# Behaviour drifts — someone changes team, or starts a project that
# involves larger pastes. A baseline computed over all time would treat
# a legitimate change of role as a permanent anomaly.
BASELINE_DAYS = 30

# Standard deviations above the mean before size counts as unusual.
#
# 3.0 rather than 2.0 deliberately. Message length is not normally
# distributed — it is heavily right-skewed, because most messages are
# short and a few are pasted documents — so a 2-sigma rule fires on
# ordinary long messages. This is a coarse instrument and the threshold
# admits it.
SIZE_SIGMA = 3.0

# The most any behavioural signal may multiply a score by.
#
# Capped because behaviour is CIRCUMSTANTIAL. It can raise the priority
# of a real finding; it must never manufacture a verdict on its own. A
# message with no findings scores zero, and zero times anything is
# still zero — which is the property that keeps this stage from
# becoming a second, unmeasurable detector.
MAX_AMPLIFICATION = 1.6


@dataclass(frozen=True)
class Baseline:
    """What one user's recent history looks like."""

    user_id: str
    events: int
    mean_chars: float
    stdev_chars: float
    seen_entity_types: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_established(self) -> bool:
        return self.events >= MIN_HISTORY


@dataclass(frozen=True)
class BehaviourSignal:
    """How unusual this message is for this user."""

    multiplier: float = 1.0
    reasons: tuple[str, ...] = ()

    @property
    def is_neutral(self) -> bool:
        return self.multiplier == 1.0


NEUTRAL = BehaviourSignal()


def build_baseline(user_id: str) -> Baseline:
    """Summarise one user's recent history.

    Reads only metadata — lengths, entity types, timestamps. It never
    touches message content, because the decision store holds none
    (D41). A behavioural profile built by a privacy tool is exactly the
    kind of thing that ought to be built from as little as possible.
    """
    from sqlalchemy import select

    from ..db.session import session_scope
    from ..models.event import DecisionEntity, DecisionEvent, _utcnow

    cutoff = _utcnow() - timedelta(days=BASELINE_DAYS)

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DecisionEvent)
                .where(DecisionEvent.user_id == user_id)
                .where(DecisionEvent.created_at >= cutoff)
            )
        )

        types = set(
            session.scalars(
                select(DecisionEntity.entity_type)
                .join(DecisionEvent)
                .where(DecisionEvent.user_id == user_id)
                .where(DecisionEvent.created_at >= cutoff)
            )
        )

    sizes = [row.char_count for row in rows]

    return Baseline(
        user_id=user_id,
        events=len(rows),
        mean_chars=statistics.fmean(sizes) if sizes else 0.0,
        # Population stdev, not sample: this is the whole window, not a
        # sample drawn from it. `pstdev` of one value is 0, which the
        # caller must guard against — a zero divisor would make every
        # subsequent message infinitely unusual.
        stdev_chars=statistics.pstdev(sizes) if len(sizes) > 1 else 0.0,
        seen_entity_types=frozenset(types),
    )


def assess(
    baseline: Baseline,
    *,
    char_count: int,
    entity_types: frozenset[str],
) -> BehaviourSignal:
    """How unusual is this message, for this user?

    :param baseline: the user's recent history
    :param char_count: length of the message being judged
    :param entity_types: what the detectors found in it
    """
    if not baseline.is_established:
        # Cold start. With no evidence, add no evidence.
        return NEUTRAL

    multiplier = 1.0
    reasons: list[str] = []

    # ---- Signal 1: unusually large ------------------------------
    # The bulk-export case. Someone who normally sends 200 characters
    # pasting 8000 is doing something different, whatever it contains.
    if baseline.stdev_chars > 0:
        z = (char_count - baseline.mean_chars) / baseline.stdev_chars

        if z >= SIZE_SIGMA:
            multiplier *= 1.3
            reasons.append(
                f"unusually large for this user "
                f"({char_count} characters against a typical {int(baseline.mean_chars)})"
            )

    # ---- Signal 2: a category never sent before -----------------
    # The most explainable signal, and the one a user can act on: "you
    # have never sent a payment card before" is a sentence that means
    # something, where "your z-score is 3.2" is not.
    novel = entity_types - baseline.seen_entity_types
    if novel:
        multiplier *= 1.25
        listed = ", ".join(sorted(t.replace("_", " ").lower() for t in novel))
        reasons.append(f"first time this user has sent {listed}")

    return BehaviourSignal(
        multiplier=min(multiplier, MAX_AMPLIFICATION),
        reasons=tuple(reasons),
    )


def signal_for(user_id: str, *, char_count: int, entity_types: frozenset[str]) -> BehaviourSignal:
    """Convenience wrapper: build the baseline and assess in one call.

    Never raises. A behavioural signal is an enhancement; if the store
    is unavailable the message must still be judged on its content,
    exactly as the decision recorder must never break protection.
    """
    try:
        return assess(
            build_baseline(user_id), char_count=char_count, entity_types=entity_types
        )
    except Exception:  # noqa: BLE001
        logger.exception("behavioural baseline unavailable for %s", user_id)
        return NEUTRAL
