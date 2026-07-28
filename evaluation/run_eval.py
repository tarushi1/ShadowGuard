"""
Evaluation harness.
================================================================

Runs the real detection pipeline over the labelled corpus and reports
precision, recall, false-positive rate and latency — overall, split by
difficulty and category, and compared against a regex-only baseline.

    python -m evaluation.run_eval
    python -m evaluation.run_eval --failures     # list every mistake
    python -m evaluation.run_eval --json out.json

Three things this does deliberately
-----------------------------------
1. It calls `orchestrator.run_stages()` — THE SAME function the API
   route calls. A harness that reimplements the stage logic measures
   code that never runs in production, and the two drift apart with
   nothing failing to say so.

2. It runs a REGEX-ONLY BASELINE alongside the full pipeline. The
   report's central claim is a difference between configurations, and
   a difference needs both sides measured the same way, on the same
   samples, in the same run.

3. It lists every misclassified sample by id. An aggregate number
   tells you that something is wrong; only the failure list tells you
   what. Every improvement in this project so far came from reading
   individual failures, not from staring at an average.
"""

import argparse
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from backend.app.pipeline import orchestrator
from backend.app.schemas.analyze import Action

from .corpus import Sample, load
from .metrics import ASSUMED_PREVALENCE, Confusion, confusion_from, percentile

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass(frozen=True)
class Outcome:
    """What the pipeline did with one sample."""

    sample: Sample
    action: Action
    found_types: tuple[str, ...]
    latency_ms: float

    @property
    def predicted_sensitive(self) -> bool:
        """A prediction is "sensitive" when the message was NOT allowed.

        This mirrors what the product actually does. `warn`, `redact`
        and `block` all put something in the user's way; `allow` is the
        only outcome where the message goes through untouched.
        """
        return self.action is not Action.ALLOW

    @property
    def correct(self) -> bool:
        return self.predicted_sensitive == self.sample.is_sensitive

    @property
    def error_kind(self) -> str | None:
        if self.correct:
            return None
        return "FN" if self.sample.is_sensitive else "FP"


def evaluate(samples: list[Sample], *, use_entropy: bool) -> list[Outcome]:
    """Run one configuration over every sample."""
    outcomes: list[Outcome] = []

    for sample in samples:
        started = time.perf_counter()
        result = orchestrator.run_stages(sample.text, use_entropy=use_entropy)
        elapsed_ms = (time.perf_counter() - started) * 1000

        outcomes.append(
            Outcome(
                sample=sample,
                action=result.action,
                found_types=tuple(sorted({f.type for f in result.findings})),
                latency_ms=elapsed_ms,
            )
        )

    return outcomes


def confusion_for(outcomes: list[Outcome]) -> Confusion:
    return confusion_from([(o.sample.is_sensitive, o.predicted_sensitive) for o in outcomes])


# ---------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------

def _rule(char: str = "-", width: int = 74) -> str:
    return char * width


def _headline(name: str, c: Confusion) -> str:
    lo, hi = c.recall_interval
    flo, fhi = c.fpr_interval

    return "\n".join(
        [
            f"{name}",
            _rule(),
            f"  samples            {c.total}",
            f"  TP {c.tp:<4} FP {c.fp:<4} TN {c.tn:<4} FN {c.fn}",
            "",
            f"  recall             {c.recall:6.1%}   [{lo:.1%} - {hi:.1%}]   <- leaks caught",
            f"  false-positive     {c.false_positive_rate:6.1%}   [{flo:.1%} - {fhi:.1%}]   <- false alarms",
            f"  precision          {c.precision:6.1%}   (on the balanced corpus)",
            f"  precision @ {ASSUMED_PREVALENCE:.0%} PR  {c.precision_at_prevalence():6.1%}   <- corrected for real traffic",
            f"  F1                 {c.f1:6.1%}",
        ]
    )


def _breakdown(title: str, groups: dict[str, list[Outcome]]) -> str:
    lines = [f"{title}", _rule()]
    lines.append(f"  {'group':<16} {'n':>4} {'recall':>8} {'FPR':>8} {'errors':>8}")

    for key in sorted(groups):
        outcomes = groups[key]
        c = confusion_for(outcomes)
        errors = c.fp + c.fn

        # Recall is meaningless for a group with no sensitive samples,
        # and FPR is meaningless with no benign ones. Printing 100% for
        # a metric with an empty denominator invents good news.
        recall = f"{c.recall:8.1%}" if (c.tp + c.fn) else "       -"
        fpr = f"{c.false_positive_rate:8.1%}" if (c.fp + c.tn) else "       -"

        lines.append(f"  {key:<16} {len(outcomes):>4} {recall} {fpr} {errors:>8}")

    return "\n".join(lines)


def _latency(outcomes: list[Outcome]) -> str:
    values = [o.latency_ms for o in outcomes]

    return "\n".join(
        [
            "LATENCY (detection only, excludes HTTP)",
            _rule(),
            f"  mean               {sum(values) / len(values):8.3f} ms",
            f"  p50                {percentile(values, 50):8.3f} ms",
            f"  p95                {percentile(values, 95):8.3f} ms",
            f"  max                {max(values):8.3f} ms",
            "",
            "  Reported at percentiles, not just the mean: the mean of a",
            "  long-tailed distribution describes an experience nobody has.",
        ]
    )


def _failures(outcomes: list[Outcome]) -> str:
    bad = [o for o in outcomes if not o.correct]
    if not bad:
        return "FAILURES\n" + _rule() + "\n  none"

    lines = ["FAILURES", _rule()]

    for kind, label, why in (
        ("FN", "FALSE NEGATIVES — sensitive content allowed through", "a leak"),
        ("FP", "FALSE POSITIVES — benign content withheld", "cries wolf"),
    ):
        group = [o for o in bad if o.error_kind == kind]
        if not group:
            continue

        lines.append("")
        lines.append(f"  {label}  ({len(group)}, {why})")

        for outcome in group:
            snippet = outcome.sample.text.replace("\n", " ")[:64]
            lines.append(f"    {outcome.sample.id}  [{outcome.sample.difficulty:<6}] {snippet}")
            if outcome.sample.expected:
                lines.append(f"          expected: {', '.join(outcome.sample.expected)}")
            if outcome.found_types:
                lines.append(f"          found:    {', '.join(outcome.found_types)}")
            if outcome.sample.note:
                lines.append(f"          note:     {outcome.sample.note}")

    return "\n".join(lines)


def _comparison(baseline: Confusion, full: Confusion) -> str:
    def delta(a: float, b: float, higher_is_better: bool) -> str:
        diff = b - a
        if abs(diff) < 0.0005:
            return "     ="
        arrow = "+" if diff > 0 else ""
        good = (diff > 0) == higher_is_better
        return f"{arrow}{diff:.1%} {'✓' if good else '✗'}"

    return "\n".join(
        [
            "REGEX-ONLY BASELINE  vs  FULL STAGE 1",
            _rule(),
            f"  {'metric':<18} {'baseline':>10} {'full':>10}   change",
            f"  {'recall':<18} {baseline.recall:>10.1%} {full.recall:>10.1%}   "
            f"{delta(baseline.recall, full.recall, True)}",
            f"  {'false-positive':<18} {baseline.false_positive_rate:>10.1%} "
            f"{full.false_positive_rate:>10.1%}   "
            f"{delta(baseline.false_positive_rate, full.false_positive_rate, False)}",
            f"  {'precision':<18} {baseline.precision:>10.1%} {full.precision:>10.1%}   "
            f"{delta(baseline.precision, full.precision, True)}",
            f"  {'F1':<18} {baseline.f1:>10.1%} {full.f1:>10.1%}   "
            f"{delta(baseline.f1, full.f1, True)}",
            "",
            "  This is the comparison the report is built on. Entropy should",
            "  raise recall (it finds unknown formats) at some cost in false",
            "  positives — and the trade needs to be stated, not hidden.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ShadowGuard's detection pipeline.")
    parser.add_argument("--failures", action="store_true", help="list every misclassified sample")
    parser.add_argument("--json", type=Path, help="also write machine-readable results here")
    args = parser.parse_args()

    samples = load()

    baseline_outcomes = evaluate(samples, use_entropy=False)
    full_outcomes = evaluate(samples, use_entropy=True)

    baseline = confusion_for(baseline_outcomes)
    full = confusion_for(full_outcomes)

    by_difficulty: dict[str, list[Outcome]] = defaultdict(list)
    by_category: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in full_outcomes:
        by_difficulty[outcome.sample.difficulty].append(outcome)
        by_category[outcome.sample.category].append(outcome)

    print()
    print(_rule("=", 74))
    print("  ShadowGuard AI — Stage 1 evaluation")
    print(f"  corpus: {len(samples)} samples "
          f"({sum(s.is_sensitive for s in samples)} sensitive / "
          f"{sum(not s.is_sensitive for s in samples)} benign)")
    print(_rule("=", 74))
    print()
    print(_headline("FULL STAGE 1  (regex + entropy)", full))
    print()
    print(_comparison(baseline, full))
    print()
    print(_breakdown("BY DIFFICULTY", by_difficulty))
    print()
    print(_breakdown("BY CATEGORY", by_category))
    print()
    print(_latency(full_outcomes))
    print()

    if args.failures:
        print(_failures(full_outcomes))
        print()

    failures = sum(1 for o in full_outcomes if not o.correct)
    if failures and not args.failures:
        print(f"  {failures} misclassified. Rerun with --failures to see which.")
        print()

    if args.json:
        payload = {
            "corpus_size": len(samples),
            "assumed_prevalence": ASSUMED_PREVALENCE,
            "baseline_regex_only": baseline.as_dict(),
            "full_stage_1": full.as_dict(),
            "by_difficulty": {k: confusion_for(v).as_dict() for k, v in by_difficulty.items()},
            "by_category": {k: confusion_for(v).as_dict() for k, v in by_category.items()},
            "latency_ms": {
                "mean": round(sum(o.latency_ms for o in full_outcomes) / len(full_outcomes), 4),
                "p50": round(percentile([o.latency_ms for o in full_outcomes], 50), 4),
                "p95": round(percentile([o.latency_ms for o in full_outcomes], 95), 4),
            },
            "failures": [
                {
                    "id": o.sample.id,
                    "kind": o.error_kind,
                    "difficulty": o.sample.difficulty,
                    "category": o.sample.category,
                    "expected": list(o.sample.expected),
                    "found": list(o.found_types),
                }
                for o in full_outcomes
                if not o.correct
            ],
        }

        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"  results written to {args.json}")
        print()


if __name__ == "__main__":
    main()
