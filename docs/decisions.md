# Decision log

Every significant choice, with the alternatives and the reasoning. This is the material that
"defend your design" questions draw on — the answer to *"why did you do it that way?"* should
never be *"I don't remember"*.

Format: **context → options → choice → consequence**. Decisions are append-only; if one is
reversed, the entry stays and a new one supersedes it.

---

## D1 — Block before send, not observe and warn
**Date:** 2026-07-26 · **Status:** locked

Interception could happen after transmission (log what was sent) or before (cancel, inspect,
replay).

**Chosen: block before send.** This is what makes the "Prevention" in Data Loss *Prevention*
true. Observe-and-warn produces an audit log of leaks that already left the building.

**Consequence:** forces the hardest engineering in the project. JavaScript cannot pause an event,
so the design becomes cancel → stash → analyse → replay. Everything in Stage 0 follows from this.

---

## D2 — ChatGPT + Gemini, via a pluggable adapter layer
**Date:** 2026-07-26 · **Status:** locked

**Chosen:** architect for multiple platforms from day one, implement ChatGPT first, add Gemini in
Week 6.

**Consequence:** all platform-specific DOM knowledge lives in one file per platform
(`extension/src/adapters/`). Adding Gemini is one new file plus one manifest line, and it *cannot*
break ChatGPT because it touches no ChatGPT code path. If the schedule slips, Gemini is the thing
that gets dropped — documented as designed-but-unimplemented, which is a respectable outcome.

---

## D3 — Quantitative evaluation is a first-class deliverable
**Date:** 2026-07-26 · **Status:** locked

Graded on demo + report + quantitative evaluation.

**Chosen:** build the labelled corpus and metrics harness in **Week 3**, not Week 8.

**Consequence:** counter-intuitive but decisive. Every later stage becomes measurable the day it
is finished, and there is never a scramble to generate numbers before submission. It also forces
the detectors/routes separation (D8), because the harness must call detection without HTTP.

---

## D4 — Plain JavaScript, no bundler
**Date:** 2026-07-26 · **Status:** active

Options: webpack/Vite, or plain files.

**Chosen: plain JS.** A bundler puts a build step between the code you write and the code Chrome
runs, so console line numbers stop matching your files — debugging hell for a beginner.

**Consequence:** no npm packages inside the extension. We do not need any. Load order is declared
in `manifest.json` and matters: each file depends on the ones above it.

---

## D5 — Capture-phase listeners on `window`
**Date:** 2026-07-28 · **Status:** active

Options: bubble-phase listener; capture on the composer; capture on `window`; monkey-patching
`window.fetch`; `declarativeNetRequest`.

**Chosen: capture on `window`.** Bubble is too late. Capture on the *target* is also insufficient —
at the target element, capture and bubble listeners fire in registration order and ChatGPT
registered first. Capture on an ancestor is the only placement the DOM spec guarantees runs first.

`declarativeNetRequest` can only block after the user hit send, cannot inspect the body, and
cannot show a modal. Patching `fetch` in the page world is genuinely more robust against UI
changes but requires breaking world isolation — noted in the report as **future work**.

---

## D6 — Three send paths, one guard set
**Date:** 2026-07-28 · **Status:** active

**Chosen:** intercept `keydown`, `click` and `submit`, all routed through one shared guard
function.

**Consequence:** blocking half the doors is the same as blocking none, and the half missed is
silent. Guards duplicated across three handlers is how one drifts out of sync, producing a bug
that only reproduces when the user sends one particular way.

---

## D7 — `event.isTrusted` as the replay mechanism
**Date:** 2026-07-28 · **Status:** active

After approval we must re-send without re-triggering our own interceptor.

**Chosen:** rely on `isTrusted`. Programmatically dispatched events are always untrusted and it
cannot be forged, so our replay passes through automatically.

**Rejected:** a boolean "am I replaying?" flag. State flags in event handlers are a reliable
source of Heisenbugs — one exception between set and clear leaves it stuck in the wrong state.

---

## D8 — Detection lives in `detectors/`, never in routes or the extension
**Date:** 2026-07-29 · **Status:** locked

**Consequence:** the evaluation harness imports detectors directly and runs hundreds of samples
in milliseconds with no server process. Detection in a route handler would mean running a server
and issuing HTTP requests per sample — slow enough that you stop rerunning it, and the numbers go
stale. **What cannot be measured cheaply does not get measured.**

---

## D9 — Closed Shadow DOM + `<dialog>.showModal()` for the modal
**Date:** 2026-07-29 · **Status:** active

Options: plain div + high z-index; `<iframe>`; Shadow DOM; Shadow DOM + `<dialog>`.

**Chosen: closed Shadow DOM containing a `<dialog>`.** Shadow DOM isolates CSS both ways — a DLP
warning rendered invisible by the host page's CSS is a failure to warn, not a cosmetic bug.
`showModal()` puts the element in the browser's top layer, beating stacking-context traps that
defeat any z-index.

`closed` rather than `open` because with `open` any page script could query our Proceed button
and click it. **Honest limit:** `closed` stops the page reaching *into* our UI; it cannot stop the
page deleting the host element. A content script is a guest and cannot fully defend against a
hostile host.

---

## D10 — The response never echoes the user's text
**Date:** 2026-07-29 · **Status:** locked

**Chosen:** entities carry `start`/`end` offsets into text the client already holds.

**Consequence:** a DLP service that returns the secret it just found has doubled the number of
places that secret exists and put a copy in every HTTP log, proxy cache and error report on the
way back. A test asserts this specifically so a future refactor cannot quietly add an
`input_text` field for debugging convenience.

The one exception is `redacted_text` for the `redact` action (Week 5), where the server returns a
*safer* version than the client already has.

---

## D11 — Logs record the decision, never the content
**Date:** 2026-07-29 · **Status:** locked

**Consequence:** server logs are copied, shipped and retained far more casually than databases.
Logging prompts would recreate, in a plaintext file, exactly the exposure this project exists to
prevent. Length and entity types are enough to debug with. Also asserted by a test.

---

## D12 — Fail open, but visibly
**Date:** 2026-07-29 · **Status:** active

When the backend is unreachable: fail open (send), fail closed (block), or configurable.

**Chosen: fail open, with a visible grey "not inspected" modal.**

**Trade-off, stated plainly:** fail-open means anyone who can stop the backend has bypassed the
control, which a real enterprise deployment would not accept. Fail-closed is the stronger posture
and what commercial DLP does. We chose open because a control that bricks ChatGPT whenever a
laptop process is not running gets uninstalled — and because being *told* a message was
uninspected is meaningfully different from being silently unprotected. **The visible notice is
what makes this defensible rather than merely convenient.**

The modal is grey, not red: *"we could not check this"* is a different statement from *"this is
dangerous"*, and colouring it like a threat trains users to ignore real threats.

---

## D13 — Permissions added one at a time
**Date:** 2026-07-26 onward · **Status:** active

Started with **zero** permissions. `storage` and `host_permissions` were added in Task 3.3, the
first task that needed them.

**Consequence:** "we request the minimum permissions necessary" is a claim the git history backs
up. Every permission is a liability with a visible install-time warning, not a free feature.

---

## D14 — Python 3.12 in a project-root venv
**Date:** 2026-07-29 · **Status:** active

System Python is Apple's 3.9.6. Installing into it can break OS tooling, and 3.9 sits at the
bottom of Presidio/spaCy's supported range — dependency pain unrelated to this project.

**Chosen:** Homebrew `python@3.12`, venv at `ShadowGuard/.venv`. Both Pythons coexist; nothing
about the existing system changed.

---

## D15 — Detectors return an internal `Finding`, not the API type
**Date:** 2026-07-29 · **Status:** active

Detectors genuinely need the matched **value** internally: the entropy stage scores it, Week 5's
redaction replaces it, and the evaluation harness reports on it. The API must never return it
([D10](#d10--the-response-never-echoes-the-users-text)).

**Chosen:** two types. `detectors/base.py::Finding` carries `value`; `schemas/analyze.py::DetectedEntity`
does not. The pipeline maps between them and drops the field.

**Consequence:** the privacy boundary is **structural** rather than a rule you must remember. To
leak a value you would have to add a field to `DetectedEntity` *and* write the mapping code — a
deliberate act. Had detectors returned the API type directly, the value would already be there and
safety would depend on remembering to strip it.

---

## D16 — Overlap resolution ranks confidence before severity
**Date:** 2026-07-29 · **Status:** active

Patterns genuinely overlap: a JWT is base64 that other rules match fragments of; a twelve-digit
Aadhaar pattern matches the first twelve digits of a sixteen-digit card.

**Original ranking (severity → confidence → length) was wrong**, and the negative tests caught it.
Aadhaar is `critical` at 0.7 confidence; `CREDIT_CARD` is `high` at 0.9 *and* Luhn-validated. A
spaced card number was therefore reported as a national identifier.

**Chosen:** rank by **confidence → severity → span length**, and tighten the Aadhaar pattern with
lookarounds so it cannot match inside a longer digit run.

**Reasoning:** severity says how bad a finding would be *if true*; confidence says how likely it is
*to be true*. Choosing between two candidate readings of the same characters is a question about
truth, not consequence.

---

## D17 — Do not flag things that are not secrets
**Date:** 2026-07-29 · **Status:** active

Stripe **test** keys (`sk_test_`) are deliberately unmatched, even though `sk_live_` is critical.
SSN ranges the SSA never issues are excluded by negative lookahead.

**Reasoning:** warnings that fire on harmless things teach people to dismiss warnings, and
dismissing warnings is how real leaks get through. **False negatives are the security failure;
false positives are the adoption failure** — and a tool people disable has a recall of zero.

---

## D18 — Entropy thresholds derived from measurement, not convention
**Date:** 2026-07-29 · **Status:** active

The widely quoted recipe is "Shannon entropy > 4.5 over 20-character tokens". We measured it
before adopting it, and it fails twice: entropy is capped at `log₂(n)` so a 20-char token can
never reach 4.5, and English prose out-scores random hex at this length.

**Chosen:** character-class diversity (≥ 2 of lowercase/uppercase/digit) as the primary
discriminator, plus charset-aware thresholds — hex ≥ 3.5 (ceiling 4.0), general ≥ 4.2 (ceiling
6.0). UUIDs and 40-char lowercase hex excluded outright.

**Consequence:** the report can state a measured basis for every threshold rather than citing a
convention that does not survive contact with data.

---

## D19 — Entropy findings cap at `medium` severity and 0.85 confidence
**Date:** 2026-07-29 · **Status:** active

Base64 images, minified JavaScript, git SHAs and UUIDs are all high-entropy and harmless.

**Chosen:** this stage can warn, never block, on its own; confidence never reaches 1.0.

**Reasoning:** the detector cannot know *what* it found, only that the characters look random.
Claiming certainty would have the risk engine weight it like a private-key header match, which it
is nothing like. Honest uncertainty is what makes weighted fusion possible in Week 5.

---

## D20 — Short-circuit after a critical finding
**Date:** 2026-07-29 · **Status:** active

**Chosen:** when Stage 1a returns a `critical` finding, skip later stages. The verdict cannot
change, and Stage 2's spaCy inference alone is ~50ms against ~0.04ms for everything currently in
the pipeline.

**Consequence:** `stages_run` in the response records what actually executed, so the optimisation
is **evidence** rather than a claim. Two tests assert both halves — that it fires on critical
input, and that it does *not* fire otherwise (short-circuiting must be conditional, not an excuse
for never running later stages).

---

## D21 — Corpus labels the action, not the entity
**Date:** 2026-07-29 · **Status:** locked

`label` answers *"should this have been allowed through?"*, not *"does this contain a detectable
entity"*.

**Reasoning:** `email me at arushi@example.com` contains a detectable entity, but `EMAIL` is `low`
severity and the correct action is **allow**. Labelling by entity presence would score the
pipeline as wrong for behaving exactly as designed. The product decides actions, so the corpus
labels actions.

---

## D22 — Balanced corpus, with the base-rate correction reported alongside
**Date:** 2026-07-29 · **Status:** locked

**Chosen:** 75/75, and the harness prints *both* balanced precision and precision corrected for an
assumed 1% real-world prevalence.

**Reasoning:** a balanced corpus gives reliable recall and FPR. It also **inflates precision** —
at *p* = 0.01, a system reporting 95% precision on balanced data achieves ~15% in reality.
Reporting only the flattering figure would be misleading, and an evaluator who knows the base-rate
fallacy will ask.

---

## D23 — JSONL, not the CSV the roadmap named
**Date:** 2026-07-29 · **Status:** active

Prompts contain commas, double quotes and newlines — the three things that break CSV.

**Consequence:** a corpus that loses a sample to a parse error still produces metrics, describing
less data than you believe, with nothing anywhere to say so. `corpus.py` additionally validates
strictly and fails on the first bad row rather than skipping it.

---

## D24 — The harness calls the same code the API calls
**Date:** 2026-07-29 · **Status:** locked

`run_stages()` was extracted from `analyze()` so the route and the evaluation harness invoke one
implementation, with `use_entropy` as a first-class parameter.

**Reasoning:** a harness that reimplements the stage logic measures code that never runs in
production, and the two drift apart with nothing failing to say so. The report's central claim is
a *difference between configurations*, so producing those configurations must be a supported
capability rather than a line someone comments out by hand.

---

## D25 — Fixing detector bugs from corpus failures, without overfitting
**Date:** 2026-07-29 · **Status:** locked

The harness immediately exposed 7 false positives. Five were fixed, two were left.

**The line drawn:**

| Principled — generalises | Overfitting — memorises |
|---|---|
| "hash-algorithm prefixes denote content digests, which are public by construction" | "exclude the literal string `51H8xKl…`" |
| "vendor test keys are documented as safe to expose" | "exclude sample H010" |

The five fixes are class-level rules. One was not tuning at all but a genuine **inconsistency in
our own design**: the regex stage deliberately ignores `sk_test_`, and the entropy stage then
flagged the same string.

A test asserts the exclusions *narrow* the detector rather than neuter it — the same random body
without a `sha512-` prefix is still caught. Without that, an exclusion list quietly grows until
nothing is detected.

**The two left:** `employee badge numbers run from 100000000000 to 999999999999` and `our sku
format is ABCDE1234F`. Both require knowing a number is being *discussed* rather than *used*. No
pattern can express that, so faking a fix would be corpus memorisation. They are Week 4's target.

---

## D26 — Stage 2 findings are additive context, not competing claims
**Date:** 2026-07-30 · **Status:** locked

Merging Stage 2 with `resolve_overlaps(stage1 + semantic)` **dropped recall from 100% to 93.3%**.
spaCy labels random tokens as `PERSON` at 0.85, and confidence-first ranking let that
`low`-severity guess displace the `medium`-severity entropy finding on the same span — five
messages containing secrets were allowed through.

Flipping back to severity-first was not available: [D16](#d16--overlap-resolution-ranks-confidence-before-severity)
exists because that gives the wrong answer for Aadhaar-vs-card.

**The cases differ in kind:**

| | Nature | Arbiter |
|---|---|---|
| Aadhaar vs credit card | competing claims about what the characters **are** | confidence |
| PERSON vs high-entropy secret | not competing — spaCy mislabelled a random token | **precedence** |

**Chosen:** Stage 1 is authoritative on spans it has already classified. Semantic findings survive
only where Stage 1 claimed nothing. Two regression tests pin both halves — that secrets are not
suppressed, and that semantic findings still appear on unclaimed spans.

---

## D27 — Semantic findings carry `low` severity
**Date:** 2026-07-30 · **Status:** active

A person's name is not a secret. People discuss colleagues, authors and public figures constantly.
What makes a name sensitive is **combination**: a name plus an account number plus an email is a
customer record.

**Chosen:** `low` severity, so Stage 2 cannot change a verdict on its own.

**Consequence, reported rather than hidden:** Stage 2 currently moves no metric. Expressing
"three weak signals together are serious" is the Week 5 risk engine's job; encoding it inside a
detector would put fusion logic where the evaluation harness cannot vary it and the report cannot
compare alternatives.

---

## D28 — Presidio restricted to an entity allowlist
**Date:** 2026-07-30 · **Status:** active

Presidio ships ~30 recognizers, many duplicating ours (`EMAIL_ADDRESS`, `CREDIT_CARD`, `US_SSN`,
`IBAN_CODE`).

**Chosen:** allowlist `PERSON`, `ORGANIZATION`, `LOCATION`, `NRP` — only what Stage 1 structurally
cannot reach. Avoiding double-counting **by construction** rather than by cleanup, so a future
Presidio release adding a recognizer cannot silently start colliding with ours.

**Unanticipated benefit:** it is a **10× speed-up**. All recognizers cost 30–230 ms per message;
four cost 2.8 ms. Narrow scope was a performance decision as much as a correctness one, which was
not apparent until measured.

---

## D29 — Weak rules are gated on nearby context words

**Context.** Two hard negatives survived every earlier fix:

```
H016  employee badge numbers run from 100000000000 to 999999999999   → INDIA_AADHAAR
H017  our sku format is ABCDE1234F which collides with the validator → INDIA_PAN
```

Neither is fixable by pattern, checksum or entropy, and the reason is structural. Every *strong*
rule in `regex_detector.py` draws its confidence from something beyond shape — a vendor prefix
(`ghp_`), a checksum (Luhn), a unique header (`-----BEGIN`). These two have nothing beyond shape.
Twelve digits is also a badge number, an order id, a phone range; five letters and four digits is
also an SKU or a part number.

The corpus contains the demonstration directly:

```
S024  the vendor submitted PAN ABCDE1234F for gst registration      → sensitive
H017  our sku format is ABCDE1234F which collides with the validator → benign
```

The **same ten characters**. The difference is whether the number is being **used** or
**discussed** — and that evidence is not in the token.

**Options considered:**

| Approach | Effect |
|---|---|
| Required context (gate) | Match dropped unless a supporting word is nearby |
| Score adjustment (Presidio's "context enhancers") | Finding kept, confidence lowered |
| Deny-phrases — suppress on "format is", "example", "run from" | Blacklist |
| ML context classifier | Needs labelled context data we do not have |

**Chosen:** the **gate**, on `INDIA_AADHAAR` and `INDIA_PAN` only, with a ±50-character window.

Score adjustment is the more elegant answer and is the wrong one *this week*: the verdict is
currently derived from **severity**, so a lower confidence would change nothing. A score reduction
that nothing reads is not a fix. Week 5's risk engine can soften the gate into a weight once
something exists that can read a weight.

Deny-phrases were rejected as a blacklist — endless, and every phrase not thought of is a false
positive. The gate is an allowlist, so the failure mode is a **miss**, which is at least visible in
recall.

**Design details that carry weight:**

- **±50 characters, not "anywhere in the message."** Unbounded matching would let `aadhaar` in the
  first paragraph validate a twelve-digit build number in the ninth.
- **`\b` around every context word.** Without it `pan` matches inside `company`, `expand`, `Japan`
  and `panel`, and the gate silently stops doing anything — the worst failure mode, because every
  other test still passes.
- **The match itself is excluded from the searched window.** A security property: include it and a
  PAN beginning with the letters `PAN` validates itself. A gate the sender can satisfy by choosing
  a convenient value is not a gate.
- **Context words were written from the domain, not from the corpus** — `uidai`, `ekyc`,
  `enrolment`, `assessee`, `gstin` are in the lists and no corpus sample needs them. That breadth
  is the evidence against overfitting, per [D25](#d25--fixing-detector-bugs-from-corpus-failures-without-overfitting).

**Consequence.** Both false positives cleared; false-positive rate 2.7% → 0%. **This trades recall
for precision** and the cost is real: `my number is 2345 6789 0123` is now missed. Accepted for
rules this weak, and stated in the report's limitations rather than omitted.

---

## D30 — The 150-sample corpus is saturated, and that is reported as a limitation

**Context.** After D29 the pipeline scores 100% recall, 0% false-positive rate, 100% precision,
F1 100%, with zero misclassified samples.

The temptation is to present that as the result. It is not one.

**The reasoning.** A perfect score on 150 samples written by the author measures the corpus, not
the detector. The Wilson interval already says so: 0 false positives in 75 benign samples places
the true rate in **[0%, 4.9%]**. Prevalence-corrected precision reads 100% at 0% FPR and **17%** at
4.9% — an enormous swing produced by a difference the corpus cannot resolve.

**Chosen:** report the saturation explicitly, in the README and in the report, and treat it as the
justification for Task 4.3 rather than as a footnote.

**Consequences, both uncomfortable and both stated:**

- **Stage 2's contribution is unmeasurable on this corpus.** It moves no metric because no headroom
  remains. It is retained for *capability* — `PERSON`/`ORGANIZATION`/`LOCATION` — not for a number.
- **"Stage 2 reduces false positives" is not demonstrated and is not claimed.** The regex-only
  baseline also sits at 100% precision here.

**Rejected:** trimming the corpus, or reporting only the balanced-precision figure. Both would
produce a better-looking table by removing the evidence that the table is unreliable.

**Note on ordering.** Task 4.3 extends the corpus *after* these results are known, which is exactly
the shape of cherry-picking. The defence has to be structural, not a promise: new samples are
written to a stated specification (categories and counts fixed in advance), the pre-extension
numbers above are kept verbatim, and both are reported side by side. If the extended corpus scores
worse, that is the finding.

---

## D31 — Evaluation is split into action level and entity level

**Context.** [D30](#d30--the-150-sample-corpus-is-saturated-and-that-is-reported-as-a-limitation)
concluded the corpus needed extending so Stage 2's contribution could be measured. Writing the
specification for that extension surfaced a problem that no number of samples could have fixed.

The chain is short and every link was already in this document:

| | |
|---|---|
| [D21](#d21--corpus-labels-the-action-not-the-entity) | the corpus labels the **action** |
| [D27](#d27--semantic-findings-carry-low-severity) | semantic findings are `low`, so the action is `allow` |
| ∴ | Stage 2 **cannot change an action** under the current policy |
| ∴ | no quantity of new samples makes Stage 2 appear in an action-level metric |

Confirmation from the other side: `metrics.py` never read `Sample.expected`. Every number the
harness printed came from `is_sensitive` vs `predicted_sensitive`. **Entity-level evaluation did
not exist**, and the field that would drive it had been loaded and ignored since Week 3.

**Chosen:** two evaluation levels, reported separately and never conflated.

| | Question | What it grades |
|---|---|---|
| **Action level** | should this message have been stopped? | the product's behaviour, including severity policy |
| **Entity level** | did we find what is actually in it? | detection quality alone |

**Consequences.**

- Stage 2 became measurable immediately: `PERSON` 79.3% recall / 69.7% precision, `ORGANIZATION`
  85.7% / 51.4%, `LOCATION` 42.9% / 37.5%.
- Two defects appeared that action-level metrics had hidden on the *existing* corpus:
  `HIGH_ENTROPY_STRING` recall was 86.7%, not 100%, and `EMAIL` precision 84.6%. Both messages had
  been judged correctly, so nothing was visible.
- The loader rule "benign ⇒ `expected` is empty" had to go — it conflated *found nothing* with
  *did nothing*, and made every semantic entity unlabellable, since all of them are low severity
  and therefore live entirely inside benign samples. Replaced by **"benign ⇒ no entity of blocking
  severity"**, checked against a registry assembled from the detectors themselves
  (`detectors/catalog.py`) rather than a hand-copied table.
- Types the corpus never labels print `-` rather than 0% precision. Reading 12 `ORGANIZATION`
  claims against silent ground truth as 12 errors would **invent bad news**, which on a security
  tool is the more persuasive failure and the easier one to miss.

---

## D32 — The corpus extension was pre-registered, and three predictions were wrong

**Context.** Extending a corpus after seeing results is the shape of cherry-picking: you already
know which way anything you add will move the numbers.

**Chosen:** borrow **pre-registration** from experimental science. Categories, counts, group
intents and six predicted outcomes were written to
[`evaluation/corpus-spec-v2.md`](../evaluation/corpus-spec-v2.md) and committed in `eb64fc4`
**before a single sample existed**. The ordering is verifiable in `git log` rather than asserted.

**Outcome: three of six predictions were correct.** Graded in §7 of that document, misses included.

That is a better result than six of six. A pre-registration where everything lands is evidence the
predictions were safe, not that the system was understood — and each miss produced something the
hits did not:

- **P1** (recall would fall to 90–93%; actual **89.0%**) missed because three samples had `EMAIL`
  as their only Stage 1 entity, and `EMAIL` is `low` severity. The specification assumed "contains
  a Stage 1 identifier" implied "action-sensitive". It does not, and finding that out is the point.
- **P4** named a threshold (≥15 points) that was invented rather than reasoned; the actual gap was
  12.9. The direction was right and the number should never have been stated.
- **P5** was graded a miss on a tie (4 spurious `PERSON` from each group) even though the looser
  reading — total spurious claims — would have passed. Regrading against the looser reading
  afterwards is exactly what pre-registration exists to prevent.

**The finding that mattered most.** **8 of the 11 false negatives had completely correct
detection.** Every expected entity was found and the message was allowed anyway, because the
severity policy cannot act on `low` findings. Week 5's target is now a measurement rather than an
aspiration: **11 points of recall recoverable by policy alone, with no detector changed.**

**The finding that was hardest to write down.** `PERSON` recall is 70.0% on Western names and
57.1% on the non-Western names of group G3 — a 12.9-point gap. The tool protects some people
measurably worse than others. It is reported in the ethics section as a result, not softened into
a caveat. Group G3 existed in the specification *specifically* to measure a weakness in a component
we chose, which is why the number exists at all.

---

## D33 — The risk engine scores by weighted fusion, not by counting

**Context.** The Week 3 placeholder took the worst single severity as the verdict. On the
200-sample corpus it allowed 11 sensitive messages through, and **8 of those had completely correct
detection** — every expected entity found, message allowed anyway, because `PERSON` and
`ORGANIZATION` are `low` and `low` means `allow` (D32).

A max-severity policy can only answer *"what is the worst single thing here?"*. It cannot express
*"a name plus an employer plus a working email address is a customer record, even though no one of
those three is sensitive."*

**The option that had to be rejected first.** Warn on two-or-more findings. Measured before
building anything else:

| | |
|---|---|
| catches | 8 of the 11 misses |
| costs | **13 benign false positives** |

Including `explain how Satya Nadella restructured Microsoft` and `compare the leadership styles of
Jacinda Ardern and Angela Merkel`. **Counting findings is not fusion** — those messages contain the
same entity types as the ones that should be stopped.

**Chosen:** a score from three components, with every number in a separate `core/policy.py`:

1. **Weighted sum**, `severity_weight × confidence`, with `decay^n` for repeats of one type — so
   ten addresses in a mailing list stay one fact rather than becoming ten.
2. **Combination bonuses** over entity *classes*, not types. `identity + contact` is the important
   one: a name identifies nobody in particular, an email address identifies nobody in particular,
   and together they are a specific contactable human being.
3. **Data-subject amplification** — a `PERSON` near words like `client`, `patient`, `candidate`,
   `performance review` weighs 4× more. This is D29's used-vs-discussed distinction one level up:
   *is this person a subject of our records, or a topic of our conversation?*

**Result: recall 89% → 98% at 0% false-positive rate.** It also produced the Stage 1 → Stage 1+2
delta that Week 4's plan promised and could not deliver: **+9 recall points**, visible at last
because the engine can finally act on `low` findings.

**Two errors caught by measurement, both fixed by principle rather than by tuning:**

- **`medium` weight of 35 lost two true positives.** Entropy confidence runs 0.50–0.85, so
  `35 × confidence` put the warn line at 0.71 — through the middle of that distribution. The fix
  came from D19's stated intent (*warn but never block*), which is a solvable constraint:
  `w × 0.50 ≥ 25` and `w × 0.85 < 90` gives **w ≥ 50**. *A weight not derived from a stated intent
  will cut some distribution in half, and you will not know which one until it costs you
  something.*
- **Amplifying `ORGANIZATION` produced a false positive**: `our test fixtures all use Foo
  Barrington as the customer name`. A data subject is a **natural person**; organisations and
  places are not. Restricting amplification to `identity` was a definitional correction, and it
  removed the false positive without touching a threshold.

**Remaining false negatives, both honest:**

- `S081` — *"we are acquiring Silverbrook Analytics next quarter"*. Material non-public information
  with no person in it. **Confidential business information is a category this system does not
  model**, and no amount of PII tuning would reach it.
- `S098` — spaCy labelled `Meenakshi Balasubramanian` as `NRP` rather than `PERSON`, so the
  identity+contact combination never fired. The non-Western-name weakness from D32, now costing a
  real miss rather than only an entity-level score.

---

## D34 — Data-subject markers are a documented threat to validity

**Context.** D33's amplification rule depends on a vocabulary of relationship words — `client`,
`patient`, `candidate`, `performance review`, `redundancy`.

**The problem, stated rather than hidden.** The corpus samples and this word list were written by
the same author. Measuring the rule on that corpus is partly measuring whether the author is
consistent with themselves. No amount of care removes that.

**Why it was still chosen.** The alternative — refusing any lexical rule — would be inconsistent
with D29, which accepted exactly this reasoning for Aadhaar and PAN. Two mitigations make it
defensible rather than merely convenient:

- The list is drawn from a **real domain vocabulary** — HR, support, clinical and compliance record
  language — not from inspecting which samples failed.
- It is **deliberately broader than the corpus needs**. `policyholder`, `beneficiary`, `grievance`
  and `offboarding` match no sample. That breadth is evidence the list was written from the domain
  rather than fitted to the data, and it is checkable by anyone.

**Consequence.** The measured benefit of this rule is **optimistic**, and validating it properly
requires prompts this project did not write. That belongs in the report's threats-to-validity
section, alongside the corpus-saturation argument in D30.

---

## D35 — Redaction re-confirms, and never edits in place

**Context.** The `redact` action needs to change the user's message. Two ways to do that.

| Option | Consequence |
|---|---|
| **Edit the composer silently, then send** | Smoother. The user never sees the substitution, and a redacted sentence can become nonsense they did not write and cannot retract. |
| **Show it, require re-confirmation** ✅ | One extra click. The user approves the exact text that will be sent. |

**Chosen: re-confirmation.** Silently rewriting somebody's message and sending it is a worse
betrayal of trust than either warning or blocking, both of which leave the user in control of their
own words. The modal previews the replacement and the button reads **Send redacted**.

The default focus goes to that button rather than to Cancel — a deliberate inversion of the
warning modal, where Cancel is the default. The proposed send is *strictly safer* than what the
user typed, so defaulting to Cancel would make the safe path the effortful one, and people take the
effortless path.

---

## D36 — Redaction replaces with a typed placeholder, never a partial mask

**Options:**

| Form | Verdict |
|---|---|
| `[REDACTED]` | Loses the context that makes the reply useful — the model cannot tell whether it is being asked about a key or a name |
| `4532 **** **** 0366` | **Rejected.** Familiar from banking UIs and wrong here |
| `[REDACTED: CREDIT_CARD]` ✅ | Names the type, leaks zero characters of the value |

**Why partial masking is the dangerous one.** It *looks* like the responsible choice, which is why
it is worth naming explicitly: the issuer prefix plus the last four digits is enough to identify a
card in most customer-service systems, and BIN + last-four is a standard reconciliation key.
**Partial masking is disclosure wearing redaction's clothes.**

A test asserts that neither the first four nor the last four characters of a secret survive.

---

## D37 — Redaction is offered only when it resolves the risk

**Context.** The first implementation offered redaction whenever the score cleared a threshold. It
produced this:

```
send the contract to [REDACTED: PERSON] at [REDACTED: ORGANIZATION],
her address is [REDACTED: EMAIL]
```

That is not a redacted message, it is a destroyed one — and offering it as a remedy teaches users
that the redact button produces garbage.

**Chosen: two conditions, both required.**

1. **Something must be left to say.** Coverage of the spans that would actually be replaced must
   stay under 50%. Someone who pastes a private key and nothing else would otherwise be handed
   `[REDACTED: PRIVATE_KEY]`, which is a receipt rather than a message.
2. **Redaction must resolve, not merely reduce.** The *residual* score — recomputed over what would
   remain — must fall below the warn threshold. Offering to redact and stopping the user anyway is
   the worst of both: their text is damaged **and** they are still blocked.

Plus a class rule: `identity` and `affiliation` are **not redactable**. A secret is *incidental* to
the sentence around it; a person is usually the *subject* of it. This does not mean personal data
is never redacted — removing the `EMAIL` from *"Miriam Feldstein can be reached at [REDACTED:
EMAIL]"* breaks the linkability that made the pair sensitive while leaving the question intact.

**Consequence: `block` becomes meaningful rather than vestigial.** It now means something specific
— *there is nothing in this message except the thing we cannot send.* Redaction is checked before
blocking, because blocking is a dead end that in practice sends people to retype the message
somewhere the tool cannot see it.

**A bug this exposed.** Coverage was first measured over *every* finding rather than over the spans
that would actually be replaced, so a message came out 70% covered when only the email address was
to be removed, and fell back to a warning it did not need.

---

## D38 — `approve()` and `discard()` were missing from `main`

**Not a decision — a defect, recorded because the failure mode is worth learning from.**

`git log -S 'function approve'` shows both functions added in `9a85fcc` and removed in `c45de5f`
("connect the extension to the FastAPI backend"), while six call sites survived.

**Why nothing caught it.** The extension has no automated tests, and the file *parses* — a
`ReferenceError` for an undeclared identifier is a runtime error. `window.__sg` is built with
`approve` as a shorthand property, so constructing it threw, `__sg` was never assigned, and every
held send became unreplayable. The symptom was `__sg is not defined` in the console, which had
already been attributed to the well-known DevTools console-context trap — a correct explanation for
a different occurrence, which is exactly how a real bug hides behind a known one.

**Restored in 5.2**, found while wiring the redaction path through the same functions.

**The lesson for the report:** at the time, the Python side had 181 tests and the extension had
none, and this was the first defect that gap actually cost. Test coverage that stops at a language
boundary leaves the other side unprotected, no matter how good the numbers look on the tested half.

**Resolved by [D40](#d40--the-extension-gets-its-own-test-suite-and-its-limits-are-stated).** Two
of the tests written there fail if this defect is reintroduced — verified by deleting the function
and watching them go red.

---

## D39 — Writing to someone else's editor, and four wrong fixes

**The hardest DOM problem in the project**, and the debugging is more valuable than the fix.

**Symptom.** Redaction reported success and ChatGPT received the original card number.

**Why it was hard to see.** `element.innerText` showed the redacted text, so every check agreed
with itself. ProseMirror keeps its own document model and treats the DOM as a *rendering* of it;
ChatGPT sends from the model. The DOM said `[REDACTED: CREDIT_CARD]` and the model said `4532…`.

**The four attempts, and what each got wrong:**

| # | Fix | Why it failed |
|---|---|---|
| 1 | `execCommand("insertText")` after setting a Range | Verified by reading `innerText` — **the same layer it had just written to** |
| 2 | Synthetic `paste` with a `DataTransfer` | Right input path, but the selection never reached the editor, so it **appended** |
| 3 | Added `execCommand("selectAll")` + a frame yield | Still appended. Still no measurement of whether the selection took |
| 4 | `selectAllChildren` + explicit `selectionchange` + instrumentation | ✅ selection covered 46 of 46 chars |

**The root cause.** ProseMirror's `DOMObserver` syncs the editor's selection from the document's
`selectionchange` event, which is delivered **asynchronously**. Setting a selection and dispatching
the paste in one synchronous block means the paste arrives while the editor still believes the
cursor is collapsed at the end — so it inserts there. Three attempts produced:

```
charge 4532015112830366 for the invoice please
charge [REDACTED: CREDIT_CARD] for the invoice please
charge [REDACTED: CREDIT_CARD] for the invoice please
charge [REDACTED: CREDIT_CARD] for the invoice please
```

**The actual lesson, and it is not about ProseMirror.** Attempts 1–3 were all reasoning about how
the editor *should* behave. Attempt 4 measured what it *did* — one number, "how many characters
does the selection cover" — and the answer was immediate and unambiguous. **The instrumentation
should have come first.** Three fixes were spent on a hypothesis that one log line would have
falsified.

**What held throughout.** The verification added after attempt 1 — re-read on a later frame, and
check that the *secret is gone* rather than that the placeholder is present — caught every one of
these failures and abandoned the send. **The card number was never transmitted across four broken
implementations.** That is the whole argument for verifying an action rather than assuming it
worked, and it is worth stating in the report exactly that way.

**One correctness detail worth keeping.** There is deliberately no `element.textContent = text`
fallback. It writes to the DOM without going through any input path, so the model is *guaranteed*
untouched — the box looks perfect and the app sends the original. **A fallback that fails
convincingly is worse than no fallback**, because the caller cannot distinguish it from success.

---

## D40 — The extension gets its own test suite, and its limits are stated

**Context.** Three defects in three days, all in JavaScript, all found by a human in a browser
([D38](#d38--approve-and-discard-were-missing-from-main),
[D39](#d39--writing-to-someone-elses-editor-and-four-wrong-fixes)). At the time, Python had **181
tests and none of the bugs**; the extension had **zero tests and all three**.

The roadmap said Week 5 continued into SQLite persistence. That was deferred, because the argument
had stopped being theoretical.

**Chosen:** Node's built-in runner (`node --test`) plus **jsdom**. No Jest, no Vitest, no build
step. The content scripts are classic scripts that attach to `globalThis.SG` — a design chosen so
they could avoid ES modules — which turns out to make them load in a test exactly as they load in
Chrome.

**Two things make the suite worth trusting.**

**Every regression test was verified by re-introducing its bug.** Deleting `function approve` fails
two tests; removing the `selectionchange` nudge fails two; making the block override reachable and
showing the original text in the redact preview fails two more. *A regression test that has never
failed is an assertion about code, not a test of it.*

**The fake editor models what ProseMirror was measured doing, not what it documents.** It appends on
a collapsed selection and reverts direct DOM writes, because that is what the real editor was
observed to do. Modelling the documented behaviour would have reproduced the mistake that caused
D39 — four fixes, each verified against an assumption, each wrong.

**What the suite deliberately cannot reach**, stated in `extension/tests/README.md` rather than
left for someone to discover:

- **Top-layer rendering** — the entire reason `<dialog>` was chosen over a positioned div
  ([D9](#d9--closed-shadow-dom--dialogshowmodal-for-the-modal)). JSDOM has no layout.
- **Real editor behaviour** — a model that agrees with our assumptions passes even when they are
  wrong.
- **Chrome's extension APIs** — `chrome.runtime` is a stand-in.
- **ChatGPT's actual DOM** — when they ship a redesign these tests keep passing and the extension
  stops working.

Browser verification stays a required manual step. *A test suite that overstates its reach is worse
than a small honest one*, because it converts "we did not check" into "we checked and it was fine".

**One test seam was added to production code.** `Interceptor` accepts an `isTrusted` override.
The browser sets that flag and nothing in JavaScript can forge it — which is why it is the correct
guard in production and an impossible one to exercise in a test. JSDOM defines it as a
non-configurable own property, so every event a test can construct looks synthetic, and *"a real
Enter is intercepted"* — the single most important behaviour in the class — could not be asserted
at all. Production uses the default; the override is reachable only from the isolated world, where
no page script can reach it.

**A side effect worth noting.** Writing `contract.test.js` — which parses the `Action` enum out of
the Python source and asserts `SG.ACTION` matches — immediately found **three stale claims in the
documentation**: the README said a bare `deploy key AKIA…` offers redaction (it blocks), and
`docs/testing.md` carried scores from before the risk engine existed. Documentation drifts exactly
like code, and only executable checks notice.

---

## D41 — Stage 3 is a text source, and stores nothing

**Context.** OCR could have been built as another detector, with its own rules for what is
sensitive in an image.

**Chosen:** it produces **text**, and that text goes through Stages 1 and 2 completely unchanged —
same regex rules, same entropy thresholds, same Presidio engine, same risk policy.

*A screenshot of an API key is not a new detection problem; it is the old one behind a new door.*
`detectors/ocr.py` therefore contains no rules and no scoring, and every improvement to Stage 1
automatically improves image handling.

**Storage: metadata only.** Neither the image nor the extracted text is kept. The image is decoded
in memory and discarded when the call returns; only entity *types*, counts and the verdict reach
the decision store.

Rejected alternatives and why:

| Option | Why not |
|---|---|
| Store the extracted OCR text | It *is* the secret the tool just found — it would contradict the entire premise of the decision store |
| Store the image | **A screenshot is more sensitive than the text inside it.** It also carries hostnames, usernames, window titles, adjacent tabs and whatever else was on screen. It would also need encryption at rest, access control, and a very good answer to *"why does the security tool have a folder of my screenshots?"* |

**Ground truth for evaluation comes from rendering the corpus into images**
(`evaluation/render.py`), so image contents are known by construction — no hand-transcription, no
second OCR engine grading the first, and fully reproducible.

**The caveat that governs how these numbers may be quoted:** rendered text is *cleaner* than a real
screenshot — no compression artefacts, no window chrome, no cursor sitting on a character. Every
OCR figure in this project is an **upper bound**.

---

## D42 — Repairing OCR damage, and the two things it cannot fix

**Context.** The first measurement was worse than expected in a specific way. Overall retention was
86.7%, but the aggregate hid the result:

```
PERSON, LOCATION, AWS_ACCESS_KEY_ID, INDIA_PAN, AADHAAR      100%
CREDIT_CARD 93% · HIGH_ENTROPY_STRING 92% · ORGANIZATION 88%
OPENAI · ANTHROPIC · GITHUB · GOOGLE · SLACK · STRIPE
  · JWT · PRIVATE_KEY                                          0%
```

**Every long mixed-case vendor key was lost — 30 findings, no survivors.** Tesseract's model is
trained on natural language and has no prior for random mixed-case strings.

Reading the actual output showed three distinct kinds of damage:

```
sk-proj-Bq7...  ->  sk-proj—Bq7...       a Unicode dash
...Qr2St6Uv...  ->  ...Qr2S t6Uv ...     spurious spaces
...Gh0bIJ       ->  ...Gh@DHIJ           substituted characters
```

The inserted spaces were the compounding failure: they split a token into fragments shorter than
the entropy stage's 20-character minimum, so **the one detector that does not need an exact match
could not rescue the ones that do.**

**Chosen:** repair the first two, refuse to guess at the third.

- **Unicode dashes → ASCII hyphen.** Unambiguous in both directions.
- **Space removal anchored to KNOWN VENDOR PREFIXES**, never applied globally. Joining every short
  token would fuse ordinary prose into invented secrets. A guard stops the repair eating the rest
  of the sentence: a fragment is only joined if it *looks random* — contains a digit, or mixes case
  — so `sk-proj-Bq7 is not working` does not become one token.
- **Character substitutions are NOT reversed.** `@` could be mapped back to `0`, but `@` appears in
  every email address, and *a transformation that corrupts real content to rescue a guess is worse
  than the miss it fixes.*

**Why an aggressive transformation is defensible here:** repair runs **only** on text that came from
an image. Typed text never passes through it, so the 98% recall and 0% false-positive rate measured
on the text corpus cannot be affected. Its blast radius is exactly the input already known to be
lossy.

**Measured, both directions** — because retention alone is a half-measurement. *Any* transformation
that makes text "more detectable" improves retention; the only thing that stops it being free is
the false positives it creates.

| | before repair | after |
|---|---|---|
| detection retention | 86.7% | **93.9%** |
| OCR-induced false positives | — | **2.0%** |

**The remaining 2% is the mirror image of the original problem, and worth stating that way.**
Both false positives are benign hex strings — a git SHA and a `sha256:` digest — that OCR split and
corrupted:

```
7f3d9a2b8c4e6015d3f7a9b2c8e4061d5a3f7b9c
7f3d9a2b8c4e6015d3f 7a9b2c8e4061d5a3Ff7b9c
```

The entropy stage recognises a git SHA as benign because it is pure hex with a known shape. Break
it and corrupt one character and it stops looking like a SHA and starts looking like a secret.
**OCR damage defeats exact-match suppression exactly as it defeats exact-match detection**, and any
tool that adds an image path inherits both.

Accepted rather than fixed: the obvious remedy — widening the digest-marker lookback so a `sha256:`
earlier in the line suppresses later fragments — is speculative tuning on two samples, which is the
line D25 draws.

**Predictions, graded.** Five were registered before the first run and a sixth before the repair
was measured. **Four of six correct.**

- **P2 was wrong and backwards.** `CREDIT_CARD` was predicted to degrade *most*, since Luhn gives
  no partial credit for a single wrong digit. It retained **93%** — spaced digit groups are close
  to ideal OCR input, and the checksum never got the chance to be the weak link. The reasoning was
  sound; the premise was false.
- **P3 was only half right.** `PERSON` did survive at 100%, but so did four structured types, so
  "semantic degrades least" was not the distinction. The real predictor is **token shape**: short,
  single-case or digit-only tokens survive; long mixed-case ones do not.

---

## D43 — Images are intercepted at ATTACH, not at send

**Context.** Text is intercepted at send: the user presses Enter, we cancel, analyse, and replay.
The obvious symmetry is to do the same for images — inspect everything at the moment of sending.

**That would be too late, and not by a little.** When a user attaches an image, ChatGPT uploads it
to its own servers immediately and puts a thumbnail in the composer referencing a remote id. By
send time:

- the bytes have already left the machine
- the composer holds a reference, not a file
- there is nothing left for us to read, let alone stop

Warning at send would mean reporting a leak that had already happened — the observe-and-warn
design [D1](#d1--block-before-send-not-observe-and-warn) rejected in the first hour of this project.

**Chosen:** intercept at attach, on two paths that need different handling.

| Path | Mechanism | Why it differs |
|---|---|---|
| **Paste** | `paste` is cancellable — `preventDefault()` and the page never sees the file | The clean case |
| **File input** | `change` is *not* meaningfully cancellable: the browser has already populated `input.files` and the page's handler is next. Emptying the input is what actually prevents the upload | Requires putting the file **back** on approval |

**Consequence:** a separate `ImageInterceptor` rather than an extension of the text one. They cancel
different things at different points in the user's flow, and merging them would produce one class
with two unrelated lifecycles.

**The replay problem returns, and is solved differently.** An approved image is re-dispatched, and
without a marker the interceptor would catch its own replay forever. Keystrokes use `isTrusted`,
which is unforgeable — but a replayed `File` is a perfectly ordinary object. A `WeakSet` of
approved files serves instead: it holds no state that can get stuck, and entries vanish when the
file is collected. Removing that guard makes the extension test suite fail with a stack overflow,
which is the loop made visible.

---

## D44 — Base64 over the message channel, multipart over HTTP

**Context.** [D41](#d41--stage-3-is-a-text-source-and-stores-nothing) rejected base64 for the HTTP
hop and used multipart. The content script then has to get the image to the service worker, and
`chrome.runtime.sendMessage` serialises through JSON — a `Blob` or `ArrayBuffer` arrives as `{}`,
silently, with no error anywhere.

**Chosen:** base64 for the content-script → worker hop, multipart from the worker onward.

The apparent inconsistency is the point: **the two hops have different constraints.** Over HTTP we
control both ends and multipart avoids a 33% size penalty on every screenshot. Over Chrome's
message channel there is no choice at all. The penalty is paid once, in memory, on a local machine
— and the request that actually leaves the worker is still multipart.

Worth stating in the report because "why base64 here and not there?" is exactly the kind of
question an examiner asks about an apparent contradiction.

---

## D45 — The second adapter revealed the first was carrying generic code

**Context.** D2 claimed a second platform would be one new file that cannot break the first. Adding
`gemini.js` tested that claim, and it failed immediately — but not in the way expected.

**What actually happened.** The new adapter declared selectors, `readText` and `isEmpty`, and
omitted `matchSendButton`, `findSendButton` and `send`. `registerAdapter` throws on a missing
method, **and the throw escaped the adapter's IIFE and took down the content script for BOTH
platforms.** The architecture that was supposed to make platforms independent had a shared failure
point nobody had exercised, because there had only ever been one adapter.

The extension test suite caught it in a single command, which is the fourth defect it has found
since D40.

**Chosen:** move the generic implementations into the registry as **defaults**, merged in before
the required-key check. `findSendButton` is *"try each selector, skip disabled and hidden"*;
`matchSendButton` is *"does the click target sit inside one of these"*; `send` is *"click it, or
fall back to a synthetic Enter"*. **None of that was ever platform-specific** — it only knows about
the selectors an adapter declares.

With defaults in place, adding a platform really is one list of CSS selectors, which is what D2
claimed and what a second adapter turned from a claim into a fact.

**`chatgpt.js` was deliberately NOT refactored to use them.** It keeps its own versions, which
handle a ChatGPT-specific case — excluding the STOP button that replaces send while a reply
streams — and whose behaviour is pinned by tests that must keep passing unchanged. *Rewriting the
working platform to tidy up the new one risks the thing that works to benefit the thing that does
not.* All 49 existing tests passed unchanged after the change, which is the evidence that the claim
held.

**What is verified, and what is not.** The architecture is verified: the registry resolves Gemini,
the interceptor and modal need no changes, ChatGPT is untouched, and tests assert all three. **The
CSS selectors are not** — they are written from Gemini's published structure and the conventions
Angular Material and Quill follow, and have never met the live site. That is stated in the
adapter file itself rather than left for a marker to discover.

The tiering is what makes the unverified part survivable: the console logs which tier matched, and
anything below tier 2 means the adapter needs re-pointing rather than silently reading the wrong
element.

---

## D46 — Passwords: the gap a user found by pasting one

**Context.** The author pasted a real password into a chat and asked why it was not caught. It was
not, and neither was any other credential:

```
this is my gmail password - ******        allow   score 0   []
my password is Tr0ub4dor                  allow   score 0   []
login: admin  pwd: letmein123             allow   score 0   []
postgres://user:s3cret@localhost/app      allow   score 0   []
```

**Nothing in the system could have caught them, and the reason is instructive.** A password has
**no format**. `sparrow419` is shaped exactly like a username, a filename or a variable name — there
is no pattern to match, no checksum to validate, no vendor prefix to anchor on. And a short
lowercase-and-digit string sits below the entropy stage's 20-character minimum and would fail its
threshold anyway.

**The only evidence is the label beside it**, which makes this the purest application of
[D29](#d29--weak-rules-are-gated-on-nearby-context-words)'s principle in the codebase. Every other
rule matches the secret and optionally checks its surroundings; this one **matches the surroundings
in order to find the secret**.

**Chosen:** two rules, deliberately at different severities.

| Rule | Severity | Why |
|---|---|---|
| `PASSWORD` | **high**, 0.75 confidence | Rests on English word order — the weakest evidence in the file |
| `CONNECTION_STRING_PASSWORD` | **critical**, 0.95 | `scheme://user:secret@host` is unambiguous; a colon-separated secret before an `@` in a URI is what a connection string *is* |

`PASSWORD` is **not critical**, and that was a deliberate refusal. Every critical rule here is
anchored to something unforgeable — a vendor prefix, a PEM header, a Luhn checksum. This one is
anchored to grammar. *Blocking on weak evidence is how a control loses the authority to block on
strong evidence.*

**Three design details that carry the precision:**

- **`group=1`.** The pattern matches `password is X` to locate the secret and reports only `X`.
  Without it, redaction produces `[REDACTED: PASSWORD]` and the sentence loses its subject; with
  it, `password is [REDACTED: PASSWORD]` still reads as something someone can answer. This required
  a new `group` field on `Rule` — the first rule in the project whose match is wider than its
  finding.
- **`(?<![A-Za-z0-9])` instead of `\b`.** An underscore is a word character, so `\b` never fires
  after one and `MYSQL_PWD=` would not match — and pasted config is exactly where credentials
  appear. The lookbehind still refuses `compass:` and `bypass:`.
- **Separators are asymmetric.** `=` and `:` need no spaces; the word and dash forms *require*
  them. Otherwise `password-protected` and `password-based` read as disclosures.

Plus two negative lists: stopwords (`too`, `wrong`, `expired`, `short`) so *sentences about*
passwords are not disclosures, and placeholders (`********`, `changeme`, `<yourpassword>`) because
flagging a stand-in trains people to dismiss the warning — [D17](#d17--do-not-flag-things-that-are-not-secrets)
applied to credentials.

**Measured: no regression.** Recall 98.0% → 98.1%, false-positive rate stays **0%**, and regex-only
recall rose 76% → 78.1%. Ten corpus samples added — five credentials, five hard negatives written
to defeat this specific rule.

**The wider point for the report.** This gap survived six weeks, a 200-sample corpus, 231 tests and
an evaluation harness reporting 98% recall — because **every one of those measured the categories
we had thought of.** A corpus cannot find a missing detector; it can only score the detectors that
exist against the samples someone chose to write. The one thing that found it was a person using
the tool for real.

---

## Still open

| Question | Decide by |
|---|---|
| Dashboard: React vs Flask + Jinja | Week 6 |
| Store uploaded images, or only extracted OCR text? | Week 6 — a real privacy question |
| Local-only backend vs deployed | Week 8 |
| Whether `redact` edits in place or requires re-confirmation | Week 5 |
