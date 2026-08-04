"""
Recording and querying decisions.
================================================================

The only module that writes to the database, which is what makes the
"no prompt text" rule enforceable: there is exactly one place to audit.

Note the signature of `record_decision()`. It takes the REQUEST
metadata and the RESPONSE — never the request itself, and never the
text. The response has already had the user's content stripped by the
API schema, so this function could not store it even if someone tried.
The type system does the remembering.
"""

import logging

from sqlalchemy import delete, func, select

from ..models.event import DecisionEntity, DecisionEvent
from ..schemas.analyze import AnalyzeResponse
from .session import session_scope

logger = logging.getLogger(__name__)


def record_decision(
    response: AnalyzeResponse,
    *,
    user_id: str,
    platform: str,
    char_count: int,
    trigger: str | None = None,
    client_version: str | None = None,
) -> None:
    """Persist one verdict.

    `char_count` is passed in rather than derived, because deriving it
    would mean this function accepting the text — and a function that
    holds the text is one edit away from storing it.

    Never raises. A DLP tool whose PROTECTION fails because its
    BOOKKEEPING failed has the priorities backwards: the user has
    already been warned or blocked correctly by the time this runs, and
    a disk-full error must not turn that into a 500 and a message the
    extension then fails open on.

    :param response: the verdict, already stripped of user content
    :param user_id: client-supplied, and therefore not an identity (B6)
    :param platform: chatgpt, gemini, ...
    :param char_count: length of the analysed text, not the text
    """
    try:
        with session_scope() as session:
            session.add(
                DecisionEvent(
                    request_id=response.request_id,
                    user_id=user_id,
                    platform=platform,
                    trigger=trigger,
                    client_version=client_version,
                    action=response.action.value,
                    level=response.level.value,
                    score=response.score,
                    reasons="\n".join(response.reasons) or None,
                    char_count=char_count,
                    latency_ms=response.latency_ms,
                    stages_run=",".join(response.stages_run),
                    entities=[
                        DecisionEntity(
                            entity_type=entity.type,
                            detector=entity.detector,
                            confidence=entity.score,
                            start_offset=entity.start,
                            end_offset=entity.end,
                        )
                        for entity in response.entities
                    ],
                )
            )
    except Exception:  # noqa: BLE001 - bookkeeping must never break protection
        logger.exception("failed to record decision %s", response.request_id)


# ---------------------------------------------------------------
# Queries — what the Week 7 dashboard will ask
# ---------------------------------------------------------------
# Written now, beside the schema, so the indexes above are justified by
# real queries rather than guessed at. An index added "just in case" is
# a write cost with no reader.


def recent(limit: int = 50, user_id: str | None = None) -> list[DecisionEvent]:
    """Most recent decisions, newest first."""
    with session_scope() as session:
        statement = select(DecisionEvent).order_by(DecisionEvent.created_at.desc()).limit(limit)

        if user_id:
            statement = statement.where(DecisionEvent.user_id == user_id)

        return list(session.scalars(statement))


def action_counts(user_id: str | None = None) -> dict[str, int]:
    """How many of each action — the dashboard's headline figure."""
    with session_scope() as session:
        statement = select(DecisionEvent.action, func.count()).group_by(DecisionEvent.action)

        if user_id:
            statement = statement.where(DecisionEvent.user_id == user_id)

        return {action: count for action, count in session.execute(statement)}


def entity_counts() -> dict[str, int]:
    """Which entity types actually fire in real use.

    The number that tells you whether a detector earns its latency —
    and, over time, whether a rule written for the corpus matches
    anything anyone really sends.
    """
    with session_scope() as session:
        statement = (
            select(DecisionEntity.entity_type, func.count())
            .group_by(DecisionEntity.entity_type)
            .order_by(func.count().desc())
        )

        return {entity_type: count for entity_type, count in session.execute(statement)}


def purge_older_than(days: int) -> int:
    """Delete events older than `days`. Returns how many went.

    Retention is a privacy control, not housekeeping. A DLP tool that
    keeps every decision forever becomes a permanent record of what
    each employee was working on and when — built by the team that
    promised to reduce exposure.

    Nothing calls this on a schedule yet, and that gap is stated in
    docs/security.md rather than left implied. It exists now so the
    policy is expressible, and so `ondelete="CASCADE"` plus the
    foreign-keys pragma get exercised by a test: without both, this
    would delete events and strand their entity rows, leaving the
    "deleted" data in the file.

    :param days: keep anything newer than this
    """
    from datetime import timedelta

    from ..models.event import _utcnow

    cutoff = _utcnow() - timedelta(days=days)

    with session_scope() as session:
        result = session.execute(
            delete(DecisionEvent).where(DecisionEvent.created_at < cutoff)
        )

        return result.rowcount or 0
