"""
Tests for the metric arithmetic.
================================================================

Hand-worked examples. The maths here produces the report's headline
figures, and a wrong denominator would misstate them with nothing
anywhere to catch it — the numbers would still look plausible.
"""

import pytest

from evaluation.metrics import Confusion, confusion_from, percentile


def test_counts_from_truth_prediction_pairs():
    pairs = [
        (True, True),    # TP
        (True, False),   # FN
        (False, True),   # FP
        (False, False),  # TN
        (True, True),    # TP
    ]
    c = confusion_from(pairs)

    assert (c.tp, c.fp, c.tn, c.fn) == (2, 1, 1, 1)
    assert c.total == 5


def test_precision_recall_and_fpr_by_hand():
    c = Confusion(tp=8, fp=2, tn=88, fn=2)

    assert c.precision == pytest.approx(8 / 10)
    assert c.recall == pytest.approx(8 / 10)
    assert c.false_positive_rate == pytest.approx(2 / 90)
    assert c.f1 == pytest.approx(0.8)


def test_a_detector_that_flags_nothing():
    """Precision is 1.0 by convention — it made no errors of that kind.

    Reporting recall alongside is what stops this reading as success:
    a system that flags nothing catches nothing.
    """
    c = Confusion(tp=0, fp=0, tn=75, fn=75)

    assert c.precision == 1.0
    assert c.recall == 0.0
    assert c.false_positive_rate == 0.0


def test_a_detector_that_flags_everything():
    """100% recall, and useless. The FPR is what exposes it."""
    c = Confusion(tp=75, fp=75, tn=0, fn=0)

    assert c.recall == 1.0
    assert c.false_positive_rate == 1.0
    assert c.precision == 0.5


def test_base_rate_correction_deflates_precision():
    """The single most important arithmetic in the evaluation.

    A balanced corpus implies half of all traffic is sensitive. Real
    traffic is perhaps 1%. Recall and FPR do not change with
    prevalence; precision collapses.
    """
    c = Confusion(tp=68, fp=4, tn=71, fn=7)   # ~90% recall, ~5% FPR

    assert c.precision > 0.9
    assert c.precision_at_prevalence(0.01) < 0.2
    assert c.precision_at_prevalence(0.5) == pytest.approx(c.precision, abs=0.02)


def test_perfect_classifier_is_unaffected_by_prevalence():
    """With no false positives there is nothing for the base rate to
    amplify."""
    c = Confusion(tp=75, fp=0, tn=75, fn=0)

    assert c.precision_at_prevalence(0.001) == pytest.approx(1.0)


def test_accuracy_is_misleading_at_low_prevalence():
    """Documented explicitly so the report can make the point rather
    than omit the metric and be asked why.

    A classifier that allows everything scores 99% accuracy on 1%-
    prevalence traffic while catching nothing.
    """
    c = Confusion(tp=0, fp=0, tn=99, fn=1)

    assert c.accuracy == pytest.approx(0.99)
    assert c.recall == 0.0


def test_confidence_interval_widens_on_small_samples():
    """150 samples is small. Stating the interval is what stops the
    report treating a three-point difference as an improvement."""
    wide = Confusion(tp=9, fp=0, tn=10, fn=1)
    narrow = Confusion(tp=900, fp=0, tn=1000, fn=100)

    wide_lo, wide_hi = wide.recall_interval
    narrow_lo, narrow_hi = narrow.recall_interval

    assert (wide_hi - wide_lo) > (narrow_hi - narrow_lo)


def test_confidence_interval_stays_inside_zero_to_one():
    """Why Wilson rather than the normal approximation: the latter
    produces intervals below 0% or above 100% near the extremes, which
    is exactly where detector metrics live."""
    lo, hi = Confusion(tp=75, fp=0, tn=75, fn=0).recall_interval

    assert 0.0 <= lo <= hi <= 1.0


def test_percentiles():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile([], 50) == 0.0
