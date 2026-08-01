"""
Redaction — removing findings while keeping the message useful.
================================================================

Turns

    charge 4532015112830366 for the invoice please

into

    charge [REDACTED: CREDIT_CARD] for the invoice please

This is the one place in the whole system where the server sends text
BACK to the client. Everywhere else it returns offsets, because a
response carrying the secret it just found has doubled the number of
places that secret exists. The exception is safe for exactly one
reason: what goes back is strictly SAFER than what the client already
holds, not a copy of it. That property is asserted by a test, because
it is the kind of invariant a future refactor breaks without noticing.

Why a typed placeholder
-----------------------
Three options, and the choice is a security decision rather than a
cosmetic one:

  [REDACTED]                  loses the context that makes the reply
                              useful — the model cannot tell whether it
                              is being asked about a key or a name
  4532 **** **** 0366         familiar from banking UIs and WRONG here.
                              It discloses the issuer prefix and the
                              last four digits, which together are
                              enough to identify a card in most
                              customer-service systems. Partial masking
                              is disclosure wearing redaction's clothes.
  [REDACTED: CREDIT_CARD]     names the TYPE and nothing else. The
                              model can still answer "how do I rotate
                              this?" sensibly, and zero characters of
                              the value survive.

The third is what this module produces.

Why coverage matters
--------------------
Redaction only helps when a message survives it. Someone who pastes a
private key and nothing else gets

    [REDACTED: PRIVATE_KEY]

which is not a message, it is a receipt. `coverage()` measures how much
of the text the findings occupy so the risk engine can tell the two
situations apart: redact when there is something left to say, block
when the secret IS the message.
"""

from ..detectors.base import Finding

PLACEHOLDER = "[REDACTED: {type}]"


def _merged_spans(findings: list[Finding]) -> list[tuple[int, int]]:
    """Non-overlapping spans in document order.

    `resolve_overlaps` has usually run already, but this must not
    depend on that. Two overlapping spans replaced independently would
    corrupt each other's offsets, and the corruption would show up as
    mangled output rather than as an error — the failure mode that
    silently ships.
    """
    if not findings:
        return []

    merged: list[tuple[int, int]] = []
    for start, end in sorted(f.span for f in findings):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged


def redact(text: str, findings: list[Finding]) -> str:
    """Replace every finding with a typed placeholder.

    Built right-to-left. Replacing left-to-right changes the length of
    the string under the offsets that have not been used yet, so every
    subsequent span lands in the wrong place — an off-by-a-growing-
    amount bug that looks fine on the first finding and corrupts the
    rest.

    :param text: the original text
    :param findings: what to remove
    :returns: text with every finding replaced
    """
    if not findings:
        return text

    # Type lookup by start offset, since spans may have been merged.
    type_at: dict[int, str] = {f.start: f.type for f in findings}

    result = text
    for start, end in reversed(_merged_spans(findings)):
        label = PLACEHOLDER.format(type=type_at.get(start, "SENSITIVE"))
        result = result[:start] + label + result[end:]

    return result


def coverage(text: str, findings: list[Finding]) -> float:
    """Fraction of the text occupied by findings, 0.0–1.0.

    Measured over NON-WHITESPACE characters. A message padded with
    newlines is not more redactable than the same message without them,
    and counting whitespace would make it look that way.
    """
    total = sum(1 for c in text if not c.isspace())
    if not total:
        return 0.0

    covered = sum(
        sum(1 for c in text[start:end] if not c.isspace())
        for start, end in _merged_spans(findings)
    )

    return covered / total
