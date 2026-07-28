"""
Loader for the labelled evaluation corpus.
================================================================

One place that knows the corpus file's shape, so the harness, the
tests and any analysis script all agree on it.

Why JSONL and not CSV
---------------------
The roadmap said CSV; this is a deliberate departure. Prompts contain
commas, double quotes and newlines — precisely the three things that
break CSV. Quoting rules then vary between writers, and a corpus that
silently loses a sample to a parse error is worse than useless,
because the metrics still compute and simply describe less data than
you think.

One JSON object per line survives all of it, stays greppable, and
diffs cleanly in git so a review can see exactly which sample changed.
"""

import json
from dataclasses import dataclass
from pathlib import Path

CORPUS_PATH = Path(__file__).parent / "data" / "corpus.jsonl"

LABELS = {"sensitive", "benign"}
DIFFICULTIES = {"easy", "medium", "hard"}


@dataclass(frozen=True)
class Sample:
    """One labelled prompt."""

    id: str
    text: str

    label: str
    """sensitive | benign — the GROUND TRUTH at ACTION level.

    Deliberately not "does this contain any entity". The question the
    product answers is "should this have been allowed through", so
    that is what the corpus labels and what the metrics measure.

    The distinction is not academic. A message containing only an
    email address holds a detectable entity but is correctly ALLOWED,
    so it is labelled benign. Labelling by entity presence would score
    the pipeline as wrong for behaving exactly as designed.
    """

    category: str
    """credential | financial | pii | unknown-format | source-code |
    hard-negative | ordinary — for per-category breakdowns."""

    difficulty: str
    """easy | medium | hard — lets metrics be reported split.

    An aggregate number hides everything interesting. 95% overall
    could be 100% on easy cases and 60% on hard ones, and only the
    second figure predicts real-world behaviour.
    """

    expected: tuple[str, ...]
    """Entity types a correct detector should find. Empty for benign.

    Secondary to `label`: used for per-type analysis, not for the
    headline pass/fail. A detector that flags the right message for a
    slightly wrong reason has still protected the user.
    """

    note: str = ""
    """Why this sample is in the corpus — mostly on hard negatives,
    recording which naive rule it is designed to defeat."""

    @property
    def is_sensitive(self) -> bool:
        return self.label == "sensitive"


def load(path: Path | None = None) -> list[Sample]:
    """Read and validate the corpus.

    Validation is strict and fails on the first bad row. A corpus that
    quietly drops malformed samples still produces metrics — metrics
    that describe less data than you believe they do, with nothing
    anywhere to say so.

    :raises ValueError: on any malformed or inconsistent row
    """
    source = path or CORPUS_PATH
    samples: list[Sample] = []
    seen_ids: set[str] = set()

    for line_number, raw in enumerate(source.read_text().splitlines(), start=1):
        if not raw.strip():
            continue  # blank lines group the file visually

        try:
            row = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"{source.name}:{line_number} is not valid JSON — {error}") from error

        def require(field: str):
            if field not in row:
                raise ValueError(f"{source.name}:{line_number} missing field {field!r}")
            return row[field]

        sample_id = require("id")
        label = require("label")
        difficulty = require("difficulty")
        expected = tuple(require("expected"))
        text = require("text")

        if sample_id in seen_ids:
            raise ValueError(f"{source.name}:{line_number} duplicate id {sample_id!r}")
        if label not in LABELS:
            raise ValueError(f"{sample_id}: label must be one of {sorted(LABELS)}")
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"{sample_id}: difficulty must be one of {sorted(DIFFICULTIES)}")
        if not text.strip():
            raise ValueError(f"{sample_id}: empty text")

        # The two consistency rules that keep the ground truth honest.
        # A benign sample listing expected entities, or a sensitive one
        # listing none, means the labeller was unsure — and an unsure
        # label silently corrupts every metric computed from it.
        if label == "benign" and expected:
            raise ValueError(f"{sample_id}: benign samples must expect no entities")
        if label == "sensitive" and not expected:
            raise ValueError(f"{sample_id}: sensitive samples must list expected entities")

        seen_ids.add(sample_id)
        samples.append(
            Sample(
                id=sample_id,
                text=text,
                label=label,
                category=require("category"),
                difficulty=difficulty,
                expected=expected,
                note=row.get("note", ""),
            )
        )

    if not samples:
        raise ValueError(f"{source} contained no samples")

    return samples


def summarise(samples: list[Sample]) -> str:
    """A one-glance description of what the corpus contains."""
    from collections import Counter

    def counts(key) -> str:
        return "  ".join(f"{k}={v}" for k, v in sorted(Counter(map(key, samples)).items()))

    return "\n".join(
        [
            f"samples     {len(samples)}",
            f"label       {counts(lambda s: s.label)}",
            f"difficulty  {counts(lambda s: s.difficulty)}",
            f"category    {counts(lambda s: s.category)}",
        ]
    )


if __name__ == "__main__":
    print(summarise(load()))
