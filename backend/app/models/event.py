"""
Persistence models — what a decision leaves behind.
================================================================

Two tables: one row per decision, and one row per entity found.

THE DESIGN CONSTRAINT
---------------------
There is no column anywhere in this file that can hold the user's
message. That is not an oversight and it is not a rule to remember —
it is the point.

"Do not store prompt text" is easy to state and easy to violate. The
moment someone needs to debug a false positive, adding a `text` column
is a five-line change that looks obviously helpful, and from then on
the database is the largest concentration of sensitive prompts in the
organisation — created by the tool that exists to stop exactly that.

So the boundary is structural instead:

  - no column accepts free text from the user
  - `record_decision()` takes the RESPONSE, which already carries no
    text (the API schema drops it — see schemas/analyze.py)
  - a test writes a known secret through the real endpoint and then
    searches the whole database FILE for it, bytes and all

Adding a text column would therefore require editing this file, the
recorder, and deleting a test that says in English why it exists. That
is a deliberate act rather than a convenience.

WHAT IS STORED, AND WHY IT IS ENOUGH
------------------------------------
Entity TYPES, offsets, scores, timings and the action taken. That
answers every question the dashboard and the report need — which
detectors fire, how often, how fast, how the risk distribution looks
per user — without holding a single character anyone typed.

`char_count` rather than the text is the pattern in miniature: enough
to spot "this user pastes 8000-character documents" and useless for
reconstructing what they pasted.

ON OFFSETS
----------
`start_offset` and `end_offset` are positions, not content. They are
kept because a Week 7 dashboard showing WHERE in a message secrets
tend to appear is genuinely useful, and because they are worthless
without the text they index into.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base. Every model in the project inherits it."""


def _utcnow() -> datetime:
    """Timezone-aware UTC.

    `datetime.utcnow()` returns a NAIVE datetime that claims nothing
    about its zone, so comparing it against an aware one raises, and
    comparing two of them across a daylight-saving boundary quietly
    gives the wrong answer. A retention policy that deletes the wrong
    rows is a privacy incident, not a bug report.
    """
    return datetime.now(timezone.utc)


class DecisionEvent(Base):
    """One verdict, as it happened.

    Deliberately records the DECISION and its context, never the
    content the decision was about.
    """

    __tablename__ = "decision_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    request_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    """The correlation id, shared with the HTTP response and the log line.

    This is what turns three separate systems into one traceable story
    when a user says "it blocked something it shouldn't have".
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    """Indexed because every dashboard query and the retention sweep
    filter on it, and an unindexed scan over a year of events is the
    kind of slow that gets a feature quietly switched off."""

    # ---- Who and where -------------------------------------------
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    """CLIENT-SUPPLIED, and therefore not an identity — see bypass B6.

    Anyone can send any id, so behavioural baselines built on this can
    be poisoned or evaded. A real deployment would take this from an
    authenticated session. Storing it as given, with that limitation
    documented, is honest; treating it as trustworthy would not be.
    """

    platform: Mapped[str] = mapped_column(String(32), index=True)

    source: Mapped[str] = mapped_column(String(8), default="text", index=True)
    """`text` or `image`.

    Indexed because the question "is the image path weaker than the
    text path?" is one the report has to answer with data, and B11 says
    the answer is yes. Without this column the two are indistinguishable
    after the fact.
    """

    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Measurements of the image and of what was read from it — never
    the pixels, never the extracted text (D41). Enough to notice that
    low-confidence reads correlate with non-detection; useless for
    reconstructing anything."""
    trigger: Mapped[str | None] = mapped_column(String(32), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """Which extension build produced this. Mismatches between
    extension and backend show up here rather than as mysterious
    behaviour nobody can reproduce."""

    # ---- The verdict ---------------------------------------------
    action: Mapped[str] = mapped_column(String(16), index=True)
    level: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)

    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The risk engine's explanations, newline-joined.

    Safe to store because they name entity TYPES and never matched
    values — the same property the API response depends on, asserted by
    a test at both boundaries. Kept because "why was this blocked" is
    the first question anyone asks, and a score alone cannot answer it.
    """

    # ---- Measurements --------------------------------------------
    char_count: Mapped[int] = mapped_column(Integer)
    """Length instead of content. Enough to notice someone pasting
    8000-character documents; useless for reconstructing them."""

    latency_ms: Mapped[float] = mapped_column(Float)
    stages_run: Mapped[str] = mapped_column(String(64))
    """Comma-separated. Makes the short-circuit measurable after the
    fact rather than only in a live response."""

    entities: Mapped[list["DecisionEntity"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # The dashboard's main query: this user, recently. A composite
        # index serves it in one lookup where two single-column indexes
        # would make the planner choose one and scan the rest.
        Index("ix_events_user_time", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<DecisionEvent {self.request_id} {self.action} "
            f"score={self.score} entities={len(self.entities)}>"
        )


class DecisionEntity(Base):
    """One entity found inside one decision.

    A separate table rather than a JSON blob on the event, because the
    questions worth asking are per-entity: which detector fires most,
    which types precede an override, how confidence is distributed.
    Those are one GROUP BY here and a full scan plus parsing there.
    """

    __tablename__ = "decision_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("decision_events.id", ondelete="CASCADE"), index=True
    )

    entity_type: Mapped[str] = mapped_column(String(48), index=True)
    detector: Mapped[str] = mapped_column(String(24), index=True)
    confidence: Mapped[float] = mapped_column(Float)

    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    """Positions, not content — and worthless without the text they
    index into, which is never stored."""

    event: Mapped[DecisionEvent] = relationship(back_populates="entities")

    def __repr__(self) -> str:
        return f"<DecisionEntity {self.entity_type} @{self.start_offset}:{self.end_offset}>"


# Column names that would mean prompt text had crept into the schema.
#
# Read by a test, not by application code. It exists so that adding
# `text` to either model fails a check that explains, in words, why the
# column must not be there — rather than passing silently because
# nobody happened to review that migration.
FORBIDDEN_COLUMN_NAMES = frozenset(
    {"text", "prompt", "content", "message", "body", "value", "matched", "snippet", "excerpt"}
)
