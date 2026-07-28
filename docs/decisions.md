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

## Still open

| Question | Decide by |
|---|---|
| Dashboard: React vs Flask + Jinja | Week 6 |
| Store uploaded images, or only extracted OCR text? | Week 6 — a real privacy question |
| Local-only backend vs deployed | Week 8 |
| Whether `redact` edits in place or requires re-confirmation | Week 5 |
