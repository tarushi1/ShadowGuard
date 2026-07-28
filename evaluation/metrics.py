"""
Classification metrics.
================================================================

Pure arithmetic, deliberately separated from the harness that produces
the counts. That separation is what lets the maths be unit-tested with
hand-worked examples — and getting a denominator wrong here would
misstate the report's headline figure with nothing to catch it.

The vocabulary, fixed once so it cannot slip:

    POSITIVE  = the pipeline did NOT allow the message (warn/redact/block)
    NEGATIVE  = the pipeline allowed it

    TP  correctly withheld a sensitive message
    FN  allowed a sensitive message      <- SECURITY failure: a leak
    FP  withheld a benign message        <- ADOPTION failure: crying wolf
    TN  correctly allowed a benign message

The two error types are not symmetric and must not be averaged into a
single "accuracy" figure that hides which one is happening. A false
negative leaks data. A false positive trains the user to dismiss
warnings — and a tool people dismiss has an effective recall of zero,
which makes enough false positives *become* a security failure.
"""

import math
from dataclasses import dataclass

# Assumed share of real traffic that actually contains sensitive
# content. Our corpus is balanced 50/50 for statistical reliability,
# which is not what production looks like. See `precision_at_prevalence`.
#
# 1% is a deliberately generous estimate; published DLP figures tend to
# be lower. Being generous here means the corrected precision we report
# is an upper bound rather than a flattering guess.
ASSUMED_PREVALENCE = 0.01


@dataclass(frozen=True)
class Confusion:
    """The four counts, and everything derivable from them."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def precision(self) -> float:
        """Of the messages we withheld, how many should we have?

        Undefined when nothing was flagged. Returns 1.0 there rather
        than 0.0: a system that flags nothing has made no mistakes of
        this kind. Its *recall* is what will be zero, and reporting
        both together is what stops that reading as success.
        """
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        """Of the sensitive messages, how many did we catch?

        The security metric. Every point missing here is a leak.
        """
        denominator = self.tp + self.fn
        return self.tp / denominator if denominator else 1.0

    @property
    def false_positive_rate(self) -> float:
        """Of the benign messages, how many did we wrongly withhold?

        The adoption metric. This is what determines whether people
        keep the tool installed.
        """
        denominator = self.fp + self.tn
        return self.fp / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall.

        Useful as a single comparison number BETWEEN configurations.
        Not a good headline on its own, because it weighs a leak and a
        false alarm equally and they are not equally bad.
        """
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        """Deliberately computed but never headlined.

        On a 50/50 corpus accuracy is mildly informative; on realistic
        1%-prevalence traffic, a system that allows everything scores
        99% accuracy while catching nothing. Included so the report can
        make that point explicitly rather than omitting the metric and
        being asked why.
        """
        return (self.tp + self.tn) / self.total if self.total else 0.0

    def precision_at_prevalence(self, prevalence: float = ASSUMED_PREVALENCE) -> float:
        """Precision corrected for realistic base rates.

        Our corpus is balanced 50/50 so that recall and false-positive
        rate each have enough samples behind them. But that balance
        INFLATES precision, because it implies half of all traffic is
        sensitive.

        Recall and FPR are properties of the classifier and do not
        change with prevalence. Precision does:

            P = (r · p) / (r · p + f · (1 - p))

        At p = 0.01, a system with 90% recall and a 5% false-positive
        rate reports ~95% precision on balanced data and achieves
        ~15% in reality. This is the base-rate fallacy, and it is the
        most common way security-tool evaluations mislead — including
        honestly, by people who simply never corrected for it.
        """
        r, f = self.recall, self.false_positive_rate
        numerator = r * prevalence
        denominator = numerator + f * (1 - prevalence)
        return numerator / denominator if denominator else 1.0

    def wilson_interval(self, successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
        """95% confidence interval for a proportion (Wilson score).

        Reported because 150 samples is a small corpus. A recall of
        0.90 over 75 positives carries roughly ±7 points, so a
        difference of three points between two configurations is noise,
        not an improvement. Stating the interval is what stops the
        report over-claiming.

        Wilson rather than the textbook normal approximation because
        the latter misbehaves badly near 0 and 1 — it can produce
        intervals extending below 0% or above 100%, which is where
        detector metrics usually live.
        """
        if trials == 0:
            return (0.0, 1.0)

        p = successes / trials
        denominator = 1 + z**2 / trials
        centre = (p + z**2 / (2 * trials)) / denominator
        spread = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denominator

        return (max(0.0, centre - spread), min(1.0, centre + spread))

    @property
    def recall_interval(self) -> tuple[float, float]:
        return self.wilson_interval(self.tp, self.tp + self.fn)

    @property
    def fpr_interval(self) -> tuple[float, float]:
        return self.wilson_interval(self.fp, self.fp + self.tn)

    def as_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "accuracy": round(self.accuracy, 4),
            "precision_at_1pct_prevalence": round(self.precision_at_prevalence(), 4),
            "recall_ci95": [round(v, 4) for v in self.recall_interval],
            "fpr_ci95": [round(v, 4) for v in self.fpr_interval],
        }


def confusion_from(pairs: list[tuple[bool, bool]]) -> Confusion:
    """Build a confusion matrix from (truth, prediction) pairs.

    :param pairs: (is_actually_sensitive, was_predicted_sensitive)
    """
    tp = sum(1 for truth, pred in pairs if truth and pred)
    fp = sum(1 for truth, pred in pairs if not truth and pred)
    tn = sum(1 for truth, pred in pairs if not truth and not pred)
    fn = sum(1 for truth, pred in pairs if truth and not pred)

    return Confusion(tp=tp, fp=fp, tn=tn, fn=fn)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile.

    Latency is reported at p50 and p95 rather than as a mean, because
    the mean of a long-tailed distribution describes an experience
    nobody has. The tail is what users notice.
    """
    if not values:
        return 0.0

    ordered = sorted(values)
    index = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[index]
