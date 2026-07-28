"""
Shared types for all detectors.
================================================================

Every detector — regex, entropy, Presidio, OCR — returns a list of
`Finding`. The pipeline merges them and converts to the API schema.

Why a separate internal type instead of reusing the API's
`DetectedEntity`
----------------------------------------------------------------
Because `Finding` carries the matched VALUE and `DetectedEntity`
must not.

Detectors genuinely need the matched substring internally: the
entropy stage scores it, Week 5's redaction replaces it, and the
evaluation harness reports on it. But that value must never leave the
process — a response containing the secret it just found has doubled
the number of places that secret exists.

Making these two different types means the boundary is structural. To
leak a value you would have to add a field to `DetectedEntity` and
write the mapping code, which is a deliberate act. If detectors
returned the API type directly, the value would already be there and
staying safe would depend on remembering to strip it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One piece of sensitive content located in some text.

    Frozen because findings are results, not working state. Merging
    and ranking produce new lists rather than mutating entries, which
    keeps the pipeline easy to reason about and safe to reorder.
    """

    type: str
    """Entity type, e.g. OPENAI_API_KEY, CREDIT_CARD."""

    start: int
    """Start offset in the analysed text."""

    end: int
    """End offset, exclusive."""

    score: float
    """Detector confidence, 0-1.

    Not a risk score. This says how sure the detector is that it found
    what it claims — a `-----BEGIN PRIVATE KEY-----` header is 1.0
    because nothing else looks like that, while a bare email is lower
    because the pattern also matches plenty of harmless text.
    How much that MATTERS is the risk engine's job, not the detector's.
    """

    detector: str
    """Which stage produced this: regex, entropy, presidio, ocr."""

    severity: str
    """low | medium | high | critical — how bad this type of finding is.

    Deliberately distinct from `score`. Severity is a property of the
    entity TYPE (a private key is always critical); score is this
    particular match's certainty. Fusing them is the risk engine's job.
    """

    value: str
    """The matched text. INTERNAL ONLY — never serialised to a client.

    Needed by the entropy stage, by redaction, and by the evaluation
    harness. See the module docstring for why it lives here and not in
    the API schema.
    """

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)

    def overlaps(self, other: "Finding") -> bool:
        """Do these two findings cover any of the same characters?

        Used to resolve duplicates: a JWT contains base64 that other
        patterns will happily match, so without overlap resolution one
        secret is reported three times and the score is inflated by an
        artefact of how many rules happened to fire.
        """
        return self.start < other.end and other.start < self.end


SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def resolve_overlaps(findings: list[Finding]) -> list[Finding]:
    """Keep the strongest finding where several cover the same text.

    Shared by every detector AND applied again across detectors in the
    pipeline, because overlap happens at both levels: two regex rules
    can match the same span, and the entropy stage will independently
    flag an API key the regex stage already identified precisely.

    Ranking: CONFIDENCE, then severity, then span length.

    Confidence leads deliberately, and getting this wrong was a real
    bug. Ranking by severity first lets a weak `critical` guess beat a
    strong `high` one: the Aadhaar pattern matches any standalone
    twelve digits at 0.7 confidence, so a Luhn-VALIDATED card number at
    0.9 was reported as a national id purely because Aadhaar is classed
    more severe.

    Severity says how bad a finding would be *if true*; confidence says
    how likely it is *to be true*. Choosing between two readings of the
    same characters is a question about truth, not consequence.

    Span length breaks remaining ties, because the longer match is the
    more specific one — the full JWT rather than a fragment of it.
    """
    ranked = sorted(
        findings,
        key=lambda f: (f.score, SEVERITY_ORDER.get(f.severity, 0), f.end - f.start),
        reverse=True,
    )

    kept: list[Finding] = []
    for finding in ranked:
        if not any(finding.overlaps(existing) for existing in kept):
            kept.append(finding)

    # Document order: the modal and any highlighting read more
    # naturally left to right than by score.
    return sorted(kept, key=lambda f: f.start)


def highest_severity(findings: list[Finding]) -> str | None:
    """The worst severity present, or None when nothing was found."""
    if not findings:
        return None
    return max(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 0)).severity
