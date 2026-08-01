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

---

## 7. Results — graded 2026-08-01

Everything above this line was committed in `eb64fc4`, before any sample existed. Everything below
is the outcome.

### 7.1 The predictions

| # | Predicted | Actual | |
|---|---|---|---|
| P1 | Action recall falls to 90–93% | **89.0%** | ✗ **miss** |
| P2 | Action-level FPR stays 0% | **0.0%** | ✓ hit |
| P3 | No action-level difference between Stage 1 and Stage 1+2 | identical on every metric | ✓ hit |
| P4 | G3 `PERSON` recall ≥15 points below G2 | 57.1% vs 70.0% — **12.9 points** | ✗ **miss** |
| P5 | `PERSON` precision hurt more by G6 than G5 | 4 spurious each — **tied** | ✗ **miss** |
| P6 | p95 latency rises | 3.490 ms → 3.695 ms | ✓ hit |

Three of six. That is a more credible result than six of six would have been, and each miss taught
something the hits did not.

**Why P1 missed.** 11 false negatives, not the 8 predicted. The three extra were `S086`, `S090`
and `S098` — G2/G3 samples whose only Stage 1 entity is an **`EMAIL`**, which is `low` severity and
therefore allows. §3 assumed "a Stage 1 identifier" would make a sample action-sensitive; `EMAIL`
does not. The error was in the specification, not the samples, and it exposed a policy question
nobody had asked (§7.3).

**Why P4 missed.** The direction and rough size were right, the threshold was invented. 12.9 points
is a serious gap and the prediction should have named a direction rather than a number pulled from
nowhere.

**Why P5 missed.** `PERSON` spurious counts came out 4 for G5 and 4 for G6. Total spurious claims
*were* higher for G6 (8 vs 6), but that is not what was written down, and grading against the
looser reading afterwards is exactly the move pre-registration exists to prevent.

### 7.2 The headline finding: detection is not the limiter

```
FALSE NEGATIVE  S076
  expected:  PERSON, ORGANIZATION
  found:     ORGANIZATION, PERSON        <- detection was perfect
  action:    allow                       <- and it was allowed anyway
```

**8 of the 11 false negatives had completely correct detection.** Stage 2 found every entity the
ground truth asked for, and the message went through regardless, because `PERSON` and
`ORGANIZATION` are `low` severity and `low` maps to `allow`.

This is the single most useful number 4.3 produced. It converts a vague Week 5 goal — "build a risk
engine" — into a measured target: **11 points of recall are recoverable by severity policy alone,
with no change to any detector.**

### 7.3 An unplanned finding: `name + email` is currently allowed

`S086` — *"send the signed contract to Miriam Feldstein at Halcyon Partners, her address is
m.feldstein@halcyonpartners.co"* — is allowed. Every entity is found; all are `low`.

A name, an employer and a working email address is a complete contactable identity, and under
GDPR it is unambiguously personal data. The current policy treats it as three harmless facts
because it can only reason about findings one at a time. This is precisely the case
[D27](../docs/decisions.md) predicted would need combination logic, now with a corpus sample
attached to it.

### 7.4 Stage 2 measured at last

| type | n | recall | precision | spurious |
|---|---|---|---|---|
| `PERSON` | 29 | 79.3% | 69.7% | 10 |
| `ORGANIZATION` | 21 | 85.7% | 51.4% | 17 |
| `LOCATION` | 7 | 42.9% | 37.5% | 5 |

Against §5's falsification criterion (recall below ~60% would mean Stage 2 is not worth 3 ms):
`PERSON` and `ORGANIZATION` clear it, `LOCATION` does not. **`LOCATION` is the weakest component
in the system** — it finds under half of what is there and is wrong about two thirds of what it
claims. It is a candidate for removal from the allowlist, which would also cut Stage 2's cost.

`ORGANIZATION` precision of 51.4% is the other uncomfortable number: **half of everything it
reports is wrong.** At `low` severity nothing acts on it, so it is currently harmless — but any
Week 5 fusion rule that treats an `ORGANIZATION` finding as evidence inherits a coin flip.

### 7.5 The ethical finding

`PERSON` recall on Western names is **70.0%**; on the non-Western names of G3 it is **57.1%**.

spaCy's English models are trained predominantly on Western news text, and this measures the
consequence: the tool protects people with Indian, Chinese, Arabic, Vietnamese and West African
names **measurably worse** than it protects people with Anglo-European ones.

That belongs in the report's ethics section as a measured result rather than a caveat. It is also
an argument for the layered design — the G3 samples that *were* caught were caught by
`INDIA_PAN`, `INDIA_AADHAAR` and `CREDIT_CARD`, rules written deliberately for a non-US context.
Stage 1 partially compensates for Stage 2's cultural bias, which is not a property anyone designed
and is worth saying out loud.
