"""
OCR evaluation — what does reading an image cost us?
================================================================

    python -m evaluation.ocr_eval
    python -m evaluation.ocr_eval --all-styles
    python -m evaluation.ocr_eval --failures

THE QUESTION
------------
Stage 1's precision comes from patterns that match EXACTLY. OCR
routinely confuses `0`/`O`, `1`/`l`/`I`, `5`/`S`, `8`/`B`. One
substitution inside a twenty-character key means the regex does not
fire at all — and a checksum rule fails outright, because a single
wrong digit fails Luhn.

So detection through an image must be worse than detection on typed
text. The number worth having is HOW MUCH worse, and which entity
types degrade first.

HOW GROUND TRUTH IS OBTAINED
----------------------------
By rendering the existing corpus into images (see `render.py`), which
makes the exact contents known by construction. No hand-transcription,
no second OCR engine grading the first.

The honest caveat, repeated here because it governs how these numbers
may be quoted: rendered text is CLEANER than a real screenshot — no
compression artefacts, no window chrome, no cursor sitting on a
character. **These are an upper bound.** Real screenshots do worse.

PRE-REGISTERED PREDICTIONS
--------------------------
Written before the first run, in the spirit of D32:

  P1  Overall detection retention is 85-95% at 14px.
  P2  CREDIT_CARD degrades MOST, because Luhn turns any single
      character error into a total miss, with no partial credit.
  P3  PERSON and ORGANIZATION degrade LEAST, because Presidio scores
      by context and a one-character error in a name leaves the
      surrounding sentence intact.
  P4  11px is materially worse than 14px — enough to matter, not
      enough to be useless.
  P5  HIGH_ENTROPY_STRING is resilient: character substitution
      preserves randomness, so the entropy stage should still fire
      even where the exact-match rules do not.
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass

from backend.app.detectors import ocr, semantic_detector
from backend.app.pipeline import orchestrator
from backend.app.schemas.analyze import Action

from .corpus import Sample, load
from .render import DEFAULT_STYLE, STYLES, RenderStyle, render


@dataclass(frozen=True)
class RoundTrip:
    """One sample, put through an image and back."""

    sample: Sample
    ocr_text: str
    confidence: float

    typed_action: Action
    ocr_action: Action

    typed_types: frozenset[str]
    ocr_types: frozenset[str]

    @property
    def text_exact(self) -> bool:
        """Did OCR reproduce the text character for character?

        Whitespace-normalised, because line wrapping is imposed by the
        renderer and is not an OCR error.
        """
        return " ".join(self.ocr_text.split()) == " ".join(self.sample.text.split())

    @property
    def caught_when_typed(self) -> bool:
        return self.typed_action is not Action.ALLOW

    @property
    def caught_via_ocr(self) -> bool:
        return self.ocr_action is not Action.ALLOW

    @property
    def lost(self) -> bool:
        """Detected as text, missed as an image. The failure that matters."""
        return self.caught_when_typed and not self.caught_via_ocr


def round_trip(sample: Sample, style: RenderStyle) -> RoundTrip:
    typed = orchestrator.run_stages(sample.text)
    result = ocr.extract_text(render(sample.text, style))
    through_image = orchestrator.run_stages(result.text)

    return RoundTrip(
        sample=sample,
        ocr_text=result.text,
        confidence=result.confidence,
        typed_action=typed.action,
        ocr_action=through_image.action,
        typed_types=frozenset(f.type for f in typed.findings),
        ocr_types=frozenset(f.type for f in through_image.findings),
    )


def _rule(char: str = "-", width: int = 74) -> str:
    return char * width


def _report(style: RenderStyle, trips: list[RoundTrip]) -> str:
    caught_typed = [t for t in trips if t.caught_when_typed]
    caught_ocr = [t for t in caught_typed if t.caught_via_ocr]

    retention = len(caught_ocr) / len(caught_typed) if caught_typed else 0.0
    exact = sum(1 for t in trips if t.text_exact) / len(trips)
    confidence = sum(t.confidence for t in trips) / len(trips)

    return "\n".join(
        [
            f"{style.name}",
            _rule(),
            f"  samples                {len(trips)}",
            f"  mean OCR confidence    {confidence:6.1%}",
            f"  text reproduced exactly{exact:6.1%}   <- character-level fidelity",
            "",
            f"  detected as text       {len(caught_typed)}",
            f"  still detected as image{len(caught_ocr):6d}",
            f"  DETECTION RETENTION    {retention:6.1%}   <- the number that matters",
        ]
    )


def _by_entity_type(trips: list[RoundTrip]) -> str:
    """Which entity types survive the round trip, and which do not.

    The aggregate hides the interesting result. A rule validated by a
    checksum has no partial credit — one wrong character and it is gone
    — while a semantic label degrades gracefully because the sentence
    around it is still there.
    """
    survived: dict[str, int] = defaultdict(int)
    expected: dict[str, int] = defaultdict(int)

    for trip in trips:
        for entity_type in trip.typed_types:
            expected[entity_type] += 1
            if entity_type in trip.ocr_types:
                survived[entity_type] += 1

    lines = [
        "SURVIVAL BY ENTITY TYPE",
        _rule(),
        f"  {'type':<24} {'typed':>6} {'via ocr':>8} {'kept':>8}",
    ]

    for entity_type in sorted(expected, key=lambda k: (-expected[k], k)):
        total = expected[entity_type]
        kept = survived[entity_type]
        lines.append(
            f"  {entity_type:<24} {total:>6} {kept:>8} {kept / total:>8.1%}"
        )

    return "\n".join(lines)


def _failures(trips: list[RoundTrip]) -> str:
    lost = [t for t in trips if t.lost]

    if not lost:
        return "LOST TO OCR\n" + _rule() + "\n  none"

    lines = ["LOST TO OCR  (detected as text, missed as an image)", _rule()]

    for trip in lost:
        lines.append(f"  {trip.sample.id}  conf={trip.confidence:.2f}")
        lines.append(f"    wanted : {trip.sample.text[:64]}")
        lines.append(f"    read   : {trip.ocr_text[:64]}")
        lines.append(f"    typed  : {sorted(trip.typed_types)}")
        lines.append(f"    via ocr: {sorted(trip.ocr_types)}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure what OCR costs in detection.")
    parser.add_argument("--all-styles", action="store_true", help="every render style, not just one")
    parser.add_argument("--failures", action="store_true", help="list what OCR lost")
    parser.add_argument("--limit", type=int, default=0, help="cap samples, for a quick run")
    args = parser.parse_args()

    if not ocr.available():
        raise SystemExit("tesseract is not installed — `brew install tesseract`")

    semantic_detector.warm_up()

    # Sensitive samples only. Benign ones cannot lose a detection they
    # never had, so they would dilute the retention figure without
    # informing it — the false-positive question for OCR is a separate
    # experiment on rendered benign text.
    samples = [s for s in load() if s.is_sensitive]
    if args.limit:
        samples = samples[: args.limit]

    styles = STYLES if args.all_styles else (DEFAULT_STYLE,)

    print()
    print(_rule("=", 74))
    print("  ShadowGuard AI — OCR round-trip evaluation")
    print(f"  {len(samples)} sensitive samples rendered, read back, re-analysed")
    print(_rule("=", 74))

    everything: list[RoundTrip] = []

    for style in styles:
        trips = [round_trip(sample, style) for sample in samples]
        everything.extend(trips)

        print()
        print(_report(style, trips))

    print()
    print(_by_entity_type(everything))
    print()

    if args.failures:
        print(_failures(everything))
        print()

    print("  Rendered text is cleaner than a real screenshot — no compression")
    print("  artefacts, no window chrome, no cursor on a character. Read every")
    print("  figure above as an UPPER BOUND.")
    print()


if __name__ == "__main__":
    main()
