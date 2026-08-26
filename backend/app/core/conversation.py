"""
Conversation priming — context that arrived in an earlier message.
================================================================

The gap this closes was found by a user describing how people actually
type:

    "I'm going to paste my password so you can see the format"
    "hunter2Fake"

Every detector in this system sees one message. The second of those
has no label beside it, so the PASSWORD rules — which require the
label and the value together — find nothing. The message sails
through, and nobody had to be attacking anything. **That is what makes
this the most practical hole in the system: it happens by accident.**

Why the system was stateless
----------------------------
Not an oversight. ShadowGuard promises that no message content is
retained (D11, D41) — the schema has no column that can hold a prompt.
But to know that message 2 primed message 3, something must remember
message 2 when message 3 arrives, and that is retention of exactly the
thing the project promises not to retain.

The detection gap and the privacy guarantee are the same decision seen
from two sides.

What is remembered, and what is not
-----------------------------------
Not the message. Not a fragment of it. Only **which words from a fixed,
closed vocabulary appeared** — the same context words the Stage 1 gates
already use: `password`, `aadhaar`, `api key`, `card`. Free text can
never enter this store, because nothing outside those lists is ever
looked up.

In memory only, never written to disk, expiring on a timer, and bounded
in size. A server restart forgets everything, which is the correct
behaviour for something that is a convenience for detection rather than
a record of anything.

Why this is not Stage 4
-----------------------
Stage 4 produces a MULTIPLIER and never a finding (D49), and that works
because it modulates evidence that already exists. It cannot work here.
`hunter2Fake` scores **zero**, and zero times any multiplier is still
zero. Priming has to let a detector FIRE that otherwise could not — so
it belongs at the detector layer, as an extra source of context, rather
than at the risk layer as an amplifier.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

# How long a mention stays relevant.
#
# Five minutes rather than a whole session. The claim being made is
# "these two messages are part of one thought", and that claim gets
# weaker the longer the gap. A password announced an hour ago tells you
# nothing about the string on screen now.
PRIMING_TTL_SECONDS = 300.0

# How many following messages a mention can support.
#
# Three, because the pattern being modelled is "I'll send it" → maybe a
# clarifying line → the secret. Beyond that the mention is no longer
# about what is being typed now, and every extra message is a chance to
# read an ordinary token as a credential.
PRIMING_MAX_MESSAGES = 3

# Ceiling on tracked conversations, oldest evicted first.
#
# A dictionary keyed by something a client influences is an unbounded
# memory allocation waiting to happen. This one is bounded, and the
# eviction is LRU so an active conversation is never dropped in favour
# of an abandoned one.
MAX_CONVERSATIONS = 500


@dataclass
class _Entry:
    words: frozenset[str]
    expires_at: float
    remaining: int


_store: OrderedDict[str, _Entry] = OrderedDict()


def note(conversation_id: str | None, words: frozenset[str]) -> None:
    """Record the vocabulary this message mentioned.

    Called AFTER the message has been analysed, never before, so a
    message can never prime itself. That is the same rule the Stage 1
    context gates follow by excluding the match from its own window
    (D29): evidence may not be its own justification.
    """
    if not conversation_id or not words:
        return

    existing = _store.get(conversation_id)
    merged = (existing.words | words) if existing and existing.expires_at > time.monotonic() else words

    _store[conversation_id] = _Entry(
        words=frozenset(merged),
        expires_at=time.monotonic() + PRIMING_TTL_SECONDS,
        remaining=PRIMING_MAX_MESSAGES,
    )
    _store.move_to_end(conversation_id)

    while len(_store) > MAX_CONVERSATIONS:
        _store.popitem(last=False)


def take(conversation_id: str | None) -> frozenset[str]:
    """Words still live from earlier messages, spending one message of budget.

    `take` rather than `get` because reading it is what consumes it:
    each analysed message uses one of the three the mention supports.
    A pure getter would let a conversation stay primed forever as long
    as nobody asked twice.
    """
    if not conversation_id:
        return frozenset()

    entry = _store.get(conversation_id)
    if entry is None:
        return frozenset()

    if entry.expires_at <= time.monotonic() or entry.remaining <= 0:
        _store.pop(conversation_id, None)
        return frozenset()

    entry.remaining -= 1
    if entry.remaining <= 0:
        # Spent. The words are still returned for THIS message — the
        # budget counts messages served, not messages refused.
        _store.pop(conversation_id, None)

    return entry.words


def active_conversations() -> int:
    """For the dashboard and for tests. Never exposes what is stored."""
    return len(_store)


def reset() -> None:
    """Drop everything. Used by tests, and safe to call in production."""
    _store.clear()
