# Corpus extension v2 — specification

**Written 2026-08-01, before any sample was authored.** Committed separately from the samples so
the ordering is verifiable in `git log`.

This document exists because of a methodology problem. The v1 corpus (150 samples) is **saturated**
— the pipeline scores 100% on every metric with zero misclassified samples ([D30](../docs/decisions.md)).
Extending a corpus *after* seeing results is the exact shape of cherry-picking: whatever you add,
you already know which way it will move the numbers.

The defence has to be structural rather than a promise, so:

1. Categories, counts and the intent of each group are **fixed here, in advance**.
2. Expected outcomes are **predicted here, in advance** (§5), including the ones that make the
   system look worse.
3. The v1 numbers are kept verbatim and reported **beside** the v2 numbers, never replaced.
4. If the extended corpus scores worse, that is the finding.

---

## 1. The problem this extension must solve

Stage 2 (Presidio + spaCy) moves no metric. The obvious reading is "the corpus under-covers
person/organisation PII", and the obvious fix is to add some. **Both are wrong**, or at least
incomplete, and the reasoning matters more than the fix:

| | |
|---|---|
| [D21](../docs/decisions.md) | the corpus labels the **action**, not the entity |
| [D27](../docs/decisions.md) | semantic findings carry `low` severity, so the action is `allow` |
| ∴ | **Stage 2 cannot change an action under the current policy** |
| ∴ | no quantity of new samples can make Stage 2 appear in action-level metrics |

Confirming this: `evaluation/metrics.py` never reads `Sample.expected`. Every number the harness
prints is derived from `is_sensitive` vs `predicted_sensitive`. **Entity-level evaluation does not
exist**, and the field that would drive it has been loaded and ignored since Week 3.

So the extension is two pieces of work, and the first is the one that actually matters:

- **4.3a — entity-level metrics.** Per-type recall and precision, comparing `found_types` against
  `expected`. This is where Stage 2's contribution is visible *today*.
- **4.3b — samples.** 50 new ones, specified in §3, giving 4.3a something to measure.

---

## 2. A loader rule that has to change

The loader currently enforces **benign ⇒ `expected == []`**. Under entity-level evaluation that
rule is wrong, and it is already producing a small inaccuracy in v1:

```
H035  "email me at arushi@example.com when the build finishes"
      expected: []      note: "email alone is low severity, action is allow"
```

A correct detector **does** find `EMAIL` there. The sample says it should find nothing.

`expected` is redefined to mean *"entity types a correct detector should find"*, for every sample
regardless of label. The honesty check that the old rule provided is preserved and strengthened:

> **A benign sample must not expect any entity type whose severity is `high` or `critical`.**

Same guarantee — you cannot label a leak as benign — but checked against the detectors' real
severity table rather than against emptiness. It also means raising `EMAIL` to `high` would fail
corpus validation until the affected samples are relabelled, which is the correct coupling.

This requires a single registry of entity type → severity, assembled from the three detectors.
That registry is independently useful: [docs/api.md](../docs/api.md) currently maintains the same
table by hand, where it can drift.

---

## 3. Composition — fixed in advance

**+50 samples → 200 total, 100 sensitive / 100 benign.** The 50/50 balance is preserved per
[D22](../docs/decisions.md).

One new category, `pii-semantic`, so these samples can be sliced out of the by-category breakdown.
Everything else reuses the existing vocabulary, so v1 and v2 breakdowns stay comparable.

### Sensitive (+25) — `S076`–`S100`

| Group | n | Category | What it is for |
|---|---|---|---|
| **G1 — semantic-only disclosure** | 8 | `pii-semantic` | Person / organisation / location is the **entire** payload; no regex-matchable token anywhere. A patient name at a named hospital; an employee's performance review. |
| **G2 — semantic + structured** | 10 | `pii-semantic` | Realistic customer records: name + organisation + a Stage 1 identifier. |
| **G3 — non-Western names** | 7 | `pii-semantic` | Indian, Chinese and Arabic names in the same disclosure shapes as G2. |

**G1 is expected to fail.** Every one of these eight will be a false negative today, because
semantic findings are `low` → `allow`. That is not a defect in the samples; it is the measurement
this extension exists to produce, and it gives Week 5's risk engine a concrete target. Predicting
it here, before the run, is what stops it from becoming a post-hoc excuse.

**G3 targets a known weakness honestly.** spaCy's English models are trained predominantly on
Western news text and are documented as weaker on non-Western names. Deliberately measuring the
weakness of a component we chose is uncomfortable and is the point — a DLP tool that silently
protects Indian users worse than American ones is an ethical problem, not just a metric.

### Benign (+25)

| Group | n | Category | IDs | What it is for |
|---|---|---|---|---|
| **G4 — public figures & known organisations** | 10 | `ordinary` | `B036`–`B045` | "explain how Satya Nadella restructured Microsoft". A `PERSON` is genuinely present and must still be **allowed**. |
| **G5 — fictional and placeholder names** | 8 | `hard-negative` | `H041`–`H048` | "write a story about Alice and Bob"; "use John Doe in the example". |
| **G6 — NER false friends** | 7 | `hard-negative` | `H049`–`H055` | Tools, libraries and terms spaCy mislabels: "should I use Jest or Mocha"; "Chen Dynasty pottery is on display". |

---

## 4. What each half can and cannot measure

Worth stating explicitly, because it is the trap this whole document is built around:

- **The benign additions cannot produce action-level false positives.** `PERSON` is `low` severity,
  so even a completely wrong NER label yields `allow`. G4–G6 are **entity-level precision** tests
  and nothing else. Anyone reading a flat 0% FPR after this extension and concluding "the NER traps
  were all handled" has misread the experiment.
- **G1 is the only group that moves action-level recall**, and it moves it *down*.
- **Stage 1 vs Stage 1+2 will still show zero action-level difference.** Again by design, again
  because of D27.

---

## 5. Pre-registered predictions

Recorded before the samples exist and before anything is run. Graded honestly afterwards, including
the misses.

| # | Prediction |
|---|---|
| **P1** | Action recall falls from 100% to **90–93%** (the 8 G1 samples become false negatives; 92/100 = 92%). |
| **P2** | Action-level false-positive rate stays at **0%**, because no benign addition can produce a non-`allow` action under D27. |
| **P3** | Stage 1 vs Stage 1+2 shows **no** action-level difference on any metric. |
| **P4** | Entity-level `PERSON` recall on **G3 (non-Western names) is at least 15 points below G2** (Western names). |
| **P5** | Entity-level `PERSON` precision is dragged down by **G6** more than by G5 — a product name in a technical sentence is a harder false friend than a name that is genuinely a name. |
| **P6** | p95 latency rises, because the short-circuit fires less often: G1–G3 contain no critical regex hit, so Stage 2 runs on all of them. |

**What would falsify the extension's premise.** If entity-level `PERSON`/`ORGANIZATION` recall on
G1–G3 comes out low (below ~60%), then Stage 2 is not merely unmeasured — it is not working well
enough to be worth 3 ms, and the honest conclusion is to say so rather than to keep it because it
is on the roadmap.

---

## 6. Authoring rules for the samples

- **No real personal data.** Names are invented; organisations are invented except in G4, where the
  entity being public is the whole point. No sample may contain a real person's actual identifier.
- **Every G1–G3 sample must be plausible as something a real employee would paste into ChatGPT.**
  A corpus of contrived strings measures a system nobody has.
- **Hard negatives must be written to defeat a specific, named mechanism**, recorded in `note` —
  the same rule v1 followed.
- **No sample is written by looking at what the detector currently does.** Author from the
  specification above, run once, report.
