"""
Conversation priming — context carried between messages.
================================================================

The gap these tests close was described by a user, not found by the
suite or the corpus, and it is the most PRACTICAL hole the system has
had because nobody needs to be attacking anything:

    "I'm going to paste my password so you can see the format"
    "hunter2Fake"

Both messages are innocuous alone. Together they are a disclosure.

Two properties are under test and they pull against each other. One is
that the second message is now caught. The other is that a primed
conversation does not start reading ordinary text as credentials — a
warning people learn to dismiss protects nothing, and the first version
of this rule produced false alarms on 2 of 10 benign follow-ups.
"""

import time

import pytest

from backend.app.core import conversation
from backend.app.detectors import regex_detector
from backend.app.pipeline import orchestrator
from backend.app.schemas.analyze import AnalyzeRequest


@pytest.fixture(autouse=True)
def clean_store():
    conversation.reset()
    yield
    conversation.reset()


def send(text: str, conversation_id: str | None = "tab-1"):
    return orchestrator.analyze(
        AnalyzeRequest(
            text=text, user_id="u", platform="chatgpt", conversation_id=conversation_id
        )
    )


# ---------------------------------------------------------------
# The case this exists for
# ---------------------------------------------------------------

def test_a_password_announced_then_sent_is_caught():
    assert send("I'm going to paste my password").action.value == "allow"

    result = send("hunter2Fake")

    assert "PASSWORD" in {e.type for e in result.entities}
    assert result.action.value != "allow"


def test_an_announced_aadhaar_satisfies_the_context_gate_next_message():
    """Priming is not a special credential feature.

    Stage 1's weak rules are gated on a nearby supporting word (D29).
    A word that arrived one message ago answers that gate's question —
    "is this digit run being used as an identifier?" — perfectly well.
    """
    send("here is my aadhaar for the kyc form")

    result = send("2345 6789 0123")

    assert "INDIA_AADHAAR" in {e.type for e in result.entities}


def test_a_message_cannot_prime_itself():
    """Evidence may not be its own justification.

    The same rule the Stage 1 gates follow by excluding a match from
    its own context window. Recording happens after the verdict, never
    before.
    """
    result = send("password")

    assert result.entities == []


# ---------------------------------------------------------------
# The bounds — without these it is a false-positive machine
# ---------------------------------------------------------------

def test_priming_does_not_leak_between_conversations():
    send("I will paste my password", conversation_id="tab-1")

    result = send("hunter2Fake", conversation_id="tab-2")

    assert result.entities == []


def test_a_message_with_no_conversation_id_is_never_primed():
    send("I will paste my password", conversation_id=None)

    assert send("hunter2Fake", conversation_id=None).entities == []


def test_priming_expires_after_a_few_messages():
    """Three follow-ups, then the mention is no longer about what is
    being typed now. Every extra message is another chance to read an
    ordinary token as a credential."""
    send("I will paste my password")

    for _ in range(conversation.PRIMING_MAX_MESSAGES):
        send("still looking for it")

    assert send("hunter2Fake").entities == []


def test_priming_expires_on_time(monkeypatch):
    send("I will paste my password")

    later = time.monotonic() + conversation.PRIMING_TTL_SECONDS + 1
    monkeypatch.setattr(conversation.time, "monotonic", lambda: later)

    assert send("hunter2Fake").entities == []


def test_the_store_is_bounded():
    """A dict keyed by anything a client influences is an unbounded
    allocation waiting to happen."""
    for i in range(conversation.MAX_CONVERSATIONS + 50):
        conversation.note(f"tab-{i}", frozenset({"password"}))

    assert conversation.active_conversations() <= conversation.MAX_CONVERSATIONS


# ---------------------------------------------------------------
# What is NOT a credential, even when primed
# ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text,why",
    [
        ("try Chrome 141 instead", "a capitalised ordinary word"),
        ("the file is report2024.pdf", "a filename"),
        ("python3.12 is installed", "a version string"),
        ("the ticket is ENG-4471", "a reference code"),
        ("see commit e83c5163316f89bfbde7d9ab23ca2e25604af290", "a git SHA"),
        ("our digest is sha256:9f2c1a44b7e3d8506a1c4f9b2e7d3a8c5f0b6e1d4a9c2f7b3e8d5a0c6", "a docker digest"),
        ("minified output is (function(e,t){var n=e.length,r=t||0;re", "a line of code"),
    ],
)
def test_primed_conversations_do_not_cry_wolf(text, why):
    """Each of these fired at some point while the rule was being
    tightened, and each is in the benign corpus or was found beside it.

    The long ones matter for a second reason: a git SHA or an npm
    integrity hash is a machine-generated token, and those already
    belong to the ENTROPY stage. Claiming them here would be a second
    detector answering a question that already has an owner.
    """
    primed = frozenset({"password"})

    baseline = {f.value for f in regex_detector.scan(text)}
    with_priming = {f.value for f in regex_detector.scan(text, primed)}

    assert with_priming == baseline, f"priming invented a finding on {why}"


def test_a_long_message_is_not_read_as_a_bare_credential():
    """The pattern being modelled is the follow-up that IS the secret.

    A paragraph is something else, and treating every credential-shaped
    token inside one as a password turns a narrow fix into noise.
    """
    primed = frozenset({"password"})
    text = (
        "so I tried logging in again this morning with the account we set up "
        "last week and it kept failing with error abc123def on every attempt"
    )

    assert regex_detector.scan(text, primed) == regex_detector.scan(text)


def test_priming_on_a_non_credential_word_does_not_imply_a_password():
    """Announcing a phone number must not make the next short message
    a credential. The bare-credential rule keys on credential labels
    specifically, not on any context word."""
    assert regex_detector.scan("hunter2Fake", frozenset({"phone"})) == []


# ---------------------------------------------------------------
# What may be remembered
# ---------------------------------------------------------------

def test_only_closed_vocabulary_words_are_ever_stored():
    """The privacy property the whole feature depends on.

    Free text must never enter the store: nothing outside the fixed
    context-word lists is looked up, so nothing outside them can be
    retained. Without this, carrying context between messages would be
    retaining message content — the exact thing D11 and D41 promise not
    to do.
    """
    text = "my password for the acme staging box is hunter2Fake, do not tell Priya"

    remembered = regex_detector.context_vocabulary_in(text)

    assert remembered <= set(regex_detector._VOCABULARY)
    for leaked in ("hunter2Fake", "acme", "Priya", "staging"):
        assert leaked not in remembered
        assert leaked.lower() not in remembered


def test_the_evaluation_path_is_never_primed():
    """`run_stages` defaults to unprimed, so the corpus keeps measuring
    single-message detection (the discipline D49 applies to Stage 4)."""
    assert orchestrator.run_stages("hunter2Fake").findings == []
