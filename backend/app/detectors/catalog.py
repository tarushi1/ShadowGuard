"""
The entity type registry.
================================================================

One place that knows every entity type the pipeline can produce and
how severe each one is.

Why this exists
---------------
The information already lived in three places — `regex_detector.RULES`,
a constant in `entropy_detector`, and a constant in `semantic_detector`
— which is fine, because that is where it belongs. What did not exist
was a way to ASK.

Three callers need to:

  - the corpus loader, to enforce that a sample labelled benign does
    not claim a `critical` entity is present
  - the evaluation harness, to group per-type results by severity
  - the documentation, which currently maintains the same table by
    hand in docs/api.md, where it can silently drift

This module assembles the map from the detectors rather than restating
it. A hand-written copy would be correct on the day it was written and
wrong the first time a severity changed, with nothing to say so.

Deliberately not in `base.py`: base is imported BY the detectors, so
importing them from it would be circular.
"""

from . import entropy_detector, regex_detector, semantic_detector


def _build() -> dict[str, str]:
    """Assemble entity type -> severity from the detectors themselves."""
    catalog: dict[str, str] = {
        rule.name: rule.severity for rule in regex_detector.RULES
    }

    catalog[entropy_detector.FINDING_TYPE] = entropy_detector.MAX_SEVERITY

    for entity in semantic_detector.SUPPORTED_ENTITIES:
        catalog[entity] = semantic_detector.SEMANTIC_SEVERITY

    return catalog


ENTITY_SEVERITY: dict[str, str] = _build()
"""Every entity type the pipeline can emit, mapped to its severity."""


# Severities at which the pipeline does something other than allow.
#
# Derived from the verdict table in the orchestrator: `low` maps to
# ALLOW, everything above it to WARN or BLOCK. Kept as a name rather
# than an inline comparison because the corpus loader's honesty check
# reads much better as "no blocking entity in a benign sample" than as
# "no entity whose severity index exceeds zero".
BLOCKING_SEVERITIES = frozenset({"medium", "high", "critical"})


def severity_of(entity_type: str) -> str | None:
    """Severity for one entity type, or None if it is not a real type.

    Returning None rather than a default is deliberate: a corpus that
    expects `CREDIT_CARDS` (plural, a typo) should fail validation
    loudly, not be quietly assigned a severity and pass.
    """
    return ENTITY_SEVERITY.get(entity_type)


def is_blocking(entity_type: str) -> bool:
    """Would this entity type, on its own, stop a message?

    Used by the corpus loader to check that benign samples do not claim
    to contain something that would have been blocked — which would
    mean the label and the expected entities disagree, and a corpus
    that disagrees with itself corrupts every metric computed from it.
    """
    return ENTITY_SEVERITY.get(entity_type) in BLOCKING_SEVERITIES
