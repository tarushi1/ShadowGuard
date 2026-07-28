# Evaluation

The labelled corpus and the harness that turns it into numbers. This folder is what makes claims
about ShadowGuard's accuracy *evidence* rather than assertion.

```bash
python -m evaluation.corpus          # summarise the corpus
pytest evaluation/tests              # validate its structure
```

---

## The corpus

`data/corpus.jsonl` — **150 samples**, hand-written, one JSON object per line.

| | |
|---|---|
| **Balance** | 75 sensitive / 75 benign |
| **Difficulty** | 39 easy · 24 medium · 87 hard |
| **Sensitive categories** | credential 31 · pii 14 · financial 12 · unknown-format 11 · source-code 7 |
| **Benign categories** | hard-negative 40 · ordinary 35 |

Every secret is **fake but format-valid** — real prefixes and lengths, invented bodies. Card
numbers are the published test values that satisfy Luhn.

### Schema

```json
{
  "id": "S001",
  "label": "sensitive",
  "category": "credential",
  "difficulty": "easy",
  "expected": ["OPENAI_API_KEY"],
  "text": "can you help me debug this? my env has OPENAI_API_KEY=sk-proj-...",
  "note": "optional — why this sample exists"
}
```

---

## Four methodology decisions

These determine what the numbers mean, so they belong in the report alongside the numbers
themselves.

### 1. Labels are at the *action* level, not the entity level

`label` answers **"should this have been allowed through?"** — not "does this contain a detectable
entity".

That distinction is not academic. `email me at arushi@example.com when the build finishes` contains
a detectable entity, but `EMAIL` is `low` severity and the correct action is **allow**. Labelling
by entity presence would score the pipeline as *wrong* for behaving exactly as designed.

The product decides actions. So the corpus labels actions.

### 2. The 50/50 balance is for statistics, not realism — and it inflates precision

Real traffic to an AI platform is overwhelmingly benign. Perhaps 1 in 100 messages contains
something sensitive; the true rate is lower in most organisations.

A balanced corpus gives reliable estimates of **recall** and **false-positive rate**, because both
have 75 samples behind them. But **precision measured on a balanced corpus overstates real-world
precision**, because it assumes sensitive messages are 50% of traffic.

Precision can be corrected for an assumed base rate. With recall *r*, false-positive rate *f*, and
prevalence *p*:

$$\text{precision}_{\text{real}} = \frac{r \cdot p}{r \cdot p + f \cdot (1 - p)}$$

At *p* = 0.01, a system with 90% recall and a 5% FPR has **corrected precision of ~15%**, not the
~95% the balanced corpus reports. The harness prints both. **Reporting only the balanced figure
would be misleading, and an evaluator who knows this will ask.**

### 3. Hard negatives are the point

40 of the 75 benign samples are `hard-negative`: text engineered to defeat a naive detector.

| Sample | Which naive rule it defeats |
|---|---|
| `what does the sk parameter mean in scikit-learn` | any `sk-` prefix rule |
| `order 1234567890123456 shipped this morning` | `\d{16}` without a Luhn check |
| `see commit e83c5163316f89bfbde7d9ab23ca2e25604af290` | raw entropy threshold |
| `request id 550e8400-e29b-41d4-a716-446655440000` | raw entropy threshold |
| `use sk_test_51H8xKl... in staging` | Stripe rules that ignore live/test |
| `our test fixture uses ssn 000-00-0000` | SSN regex without range exclusions |
| `the meeting bridge is 0000 0000 0000 0000` | Luhn alone (it passes) |

It is trivial to score 100% recall by flagging everything. These 40 samples are what stop that,
and they are why the headline number will not be flattering — which is the intention. **95% on a
trivial corpus is weaker evidence than 85% on a hard one.**

### 4. Difficulty is recorded so metrics can be split

An aggregate figure hides everything interesting. 95% overall could be 100% on easy samples and
60% on hard ones — and only the second number predicts real-world behaviour. The harness reports
per-difficulty and per-category breakdowns for exactly this reason.

---

## What the corpus does *not* cover

Stated plainly, because unstated gaps read as oversights.

- **Adversarial evasion.** Secrets split across lines, spaced out, described in words, or
  base64-wrapped. Deliberately excluded from this round; Stage 1 would score poorly and the
  honest reporting of that deserves its own section rather than being buried in an average.
- **Non-English text.** Detection is tuned for English. Quality elsewhere is untested.
- **Images.** OCR arrives in Week 6 and will need its own corpus of screenshots.
- **Behavioural signals.** Stage 4 scores a *user over time*, which a per-message corpus cannot
  express. It needs a session-level fixture.

---

## Threats to validity

- **Author bias.** The same person wrote the detectors and the corpus, so blind spots are shared:
  a format neither anticipated appears in neither. Partly mitigated by writing hard negatives
  first and letting them fail.
- **Synthetic prompts.** Written to be realistic, but not drawn from real traffic. Real messages
  are messier, longer, and more repetitive.
- **Small n.** 150 samples means roughly ±7% at 95% confidence on a proportion near 50%.
  Differences smaller than that between configurations are not meaningful.

---

## Files

| Path | What it is |
|---|---|
| `data/corpus.jsonl` | The labelled corpus. Source of truth, hand-curated, version-controlled. |
| `corpus.py` | Loader + strict validator. Fails on the first malformed row rather than silently dropping it. |
| `tests/` | Schema and consistency tests, run by `pytest`. |
| `results/` | Generated metrics — **gitignored**, because they are reproducible from the corpus. |
| `run_eval.py` | The harness (Task 3.7). |
