"""
Structural tests for the labelled corpus.
================================================================

The corpus is data, not code, so it cannot be type-checked or run.
These tests are the only thing standing between a typo and a metric
that silently describes less data than the report claims.

They assert the corpus is well-formed and adequately composed. They do
NOT assert that any detector performs well on it — that is the
harness's job, and conflating the two would let a failing detector be
"fixed" by editing the ground truth.
"""

from collections import Counter

from evaluation.corpus import load


def test_corpus_loads_and_validates():
    """load() is strict: it raises on the first malformed row rather
    than skipping it, because a silently dropped sample still produces
    metrics — describing less data than you believe."""
    assert len(load()) >= 150


def test_ids_are_unique():
    samples = load()
    assert len({s.id for s in samples}) == len(samples)


def test_classes_are_balanced_enough_for_reliable_metrics():
    """Both recall and false-positive rate need enough samples behind
    them. A 90/10 split would give a confident recall figure and a
    meaningless FPR."""
    counts = Counter(s.label for s in load())

    assert counts["sensitive"] >= 60
    assert counts["benign"] >= 60
    assert 0.7 <= counts["sensitive"] / counts["benign"] <= 1.4


def test_hard_negatives_are_a_substantial_share():
    """The corpus is only meaningful because of these.

    It is trivial to score 100% recall by flagging everything; hard
    negatives are what make precision cost something. Without a real
    share of them the evaluation flatters itself.
    """
    benign = [s for s in load() if s.label == "benign"]
    hard = [s for s in benign if s.category == "hard-negative"]

    assert len(hard) >= 30
    assert len(hard) / len(benign) >= 0.4


def test_every_hard_negative_explains_itself():
    """Each one is designed to defeat a specific naive rule. Recording
    which is what turns the corpus into an argument rather than a list."""
    for sample in load():
        if sample.category == "hard-negative":
            assert sample.note, f"{sample.id} has no note explaining why it is here"


def test_sensitive_samples_span_multiple_categories():
    """A corpus that is 90% API keys would report a headline number
    that says nothing about PII or financial data."""
    categories = {s.category for s in load() if s.label == "sensitive"}

    assert len(categories) >= 4


def test_difficulty_is_recorded_so_metrics_can_be_split():
    """An aggregate figure hides everything interesting: 95% overall
    could be 100% easy and 60% hard, and only the second predicts
    real-world behaviour."""
    counts = Counter(s.difficulty for s in load())

    assert counts["hard"] >= 40
    assert counts["easy"] >= 20


def test_labels_and_expected_entities_never_contradict():
    """An unsure label silently corrupts every metric computed from it.

    The benign half of this rule changed in v2. It used to demand
    `expected == ()`, which conflated "found nothing" with "did
    nothing" — and made every semantic entity unlabellable, since all
    of them are low severity and therefore appear only in benign
    samples. It was also already producing a small lie: H035 contains
    an email address and claimed to contain nothing.

    A benign sample may now record the low-severity entities it
    genuinely holds. What it may not do is claim something that would
    have stopped the message, which is the guarantee the old rule was
    really providing.
    """
    from backend.app.detectors.catalog import is_blocking, severity_of

    for sample in load():
        if sample.label == "benign":
            for entity_type in sample.expected:
                assert not is_blocking(entity_type), (
                    f"{sample.id}: benign but expects {entity_type} "
                    f"({severity_of(entity_type)} severity)"
                )
        else:
            assert sample.expected, f"{sample.id}: sensitive but expects nothing"


def test_no_sample_is_trivially_short():
    """Two-word fragments are not realistic prompts and would make the
    corpus easier than reality."""
    for sample in load():
        assert len(sample.text.split()) >= 3, f"{sample.id} is too short to be realistic"


def test_texts_are_distinct():
    """Duplicates would weight one case twice without saying so."""
    texts = [s.text for s in load()]
    assert len(set(texts)) == len(texts)
