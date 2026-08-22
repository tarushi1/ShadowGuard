# ShadowGuard AI

**A Context-Aware, Multimodal Data Loss Prevention Framework for Enterprise Interactions with Generative AI Platforms.**

ShadowGuard sits between an employee and a browser-based generative-AI platform (ChatGPT, Gemini)
and inspects everything — typed text, pasted text, uploaded images — *before* it is sent.
It computes a risk score and decides whether to **allow**, **warn**, **redact**, or **block**.

---

## 🔴 If your AI session expired — start here

**Paste this, exactly:**

> Read `README.md` and `docs/decisions.md` in this repo, in that order, before doing anything else.
> `README.md` is the session-recovery document: it has current state, the exact next task, and the
> mentoring contract I work under. `docs/decisions.md` is the decision log (D1–D51) — every choice
> and its reasoning, so you don't re-litigate settled decisions or contradict them. Then tell me
> what you understand the next task to be, and wait for me to confirm before writing code.

**Two files. That's it.** Everything else is reachable from them:

| File | What it gives a fresh session |
|---|---|
| **`README.md`** | State, next task, roadmap, how to run everything, measured results, the mentoring contract |
| **`docs/decisions.md`** | **D1–D51** — why every choice was made, and what was rejected. This is what stops a new session undoing settled work |

If it needs more, point it at the [knowledge base index](docs/README.md) — but don't paste those
up front. A session that reads everything before starting spends its context on history instead of
on the work.

**Do not paste code files.** The repo is on disk; the session can read what it needs. And check
`git log --oneline -15` yourself if you want to see what actually happened recently — the commit
messages carry the reasoning too.

---

## How to work on this project

*Written for whoever picks this up — including a fresh AI session. This is the working
agreement, not a preference.*

**Arushi is the author and is learning.** Comfortable with Python and basic ML; had never built a
Chrome extension, a FastAPI backend, an OCR pipeline, a DLP system, browser interception or an
evaluation harness before this project. **Assume implementation details need explaining.**

**Teach before implementing.** For anything non-trivial: what the goal is, the concept behind it,
why it is needed, what the alternatives are and why they lose, then the code — with the reasoning
attached rather than in a wall of unexplained lines.

**Compare options, then recommend firmly.** A list of choices with no recommendation is not help.
State the trade-off and pick one.

**Ask when a decision is genuinely the author's.** Never make a silent assumption on something
that changes the design — privacy trade-offs, thresholds, scope. Routine judgment calls should
just be made.

**One stage at a time, and verify before moving on.** Every task ends with something runnable and
a way to check it.

**Measure rather than assert.** This project is graded on quantitative evaluation. Any claim about
a number belongs in `evaluation/`, run and reported — including when the result is bad. Four
separate fixes to one bug were each "obviously correct" and each wrong ([D39](docs/decisions.md));
the one that worked came from measuring instead of reasoning.

**Teach debugging as a skill.** Explain what the symptom means and how to narrow it down, rather
than silently replacing broken code.

**It is a security project, so say the uncomfortable things.** Point out risks, false positives,
false negatives, privacy concerns, ethical problems and limitations without being asked. Bypasses
are tracked in [docs/security.md](docs/security.md) as **B1–B15**, several of which this design
created on purpose. Volunteering a weakness is far stronger than being asked about it.

**Keep `README.md` and `docs/` current at all times**, not at the end. Documentation drifts exactly
like code, and only executable checks notice — which is why a few agreements are asserted by tests
(manifest version vs `package.json`, `SG.ACTION` vs the Python enum).

---

> **This README is the session-recovery document.** It records current state, every locked
> decision, and the exact next task.
>
> **📚 For how and why things work, see the [knowledge base in `docs/`](docs/README.md).**
> This file tracks *state*; `docs/` explains *the system*. Both are kept current as work proceeds.

## Knowledge base

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | The system as a whole — components, data flow, why the boundaries sit where they do |
| [docs/concepts.md](docs/concepts.md) | Every technical concept, explained from zero: browser internals, Python tooling, evaluation metrics |
| [docs/decisions.md](docs/decisions.md) | The decision log — each choice, its alternatives, and the reasoning |
| [docs/api.md](docs/api.md) | The HTTP contract between extension and backend |
| [docs/security.md](docs/security.md) | Threat model, known bypasses, privacy, ethics, limitations |
| [docs/testing.md](docs/testing.md) | How to verify every component and debug by symptom |
| [evaluation/README.md](evaluation/README.md) | The corpus, its methodology, and the threats to validity — **report material** |
| [extension/tests/README.md](extension/tests/README.md) | The JavaScript test harness — what it covers, and what it deliberately cannot |
| [evaluation/corpus-spec-v2.md](evaluation/corpus-spec-v2.md) | The corpus extension, **pre-registered** with six predictions, then graded — three were wrong |
| [docs/literature.md](docs/literature.md) | **Literature review, gap identification and objectives** — report material, rubric items 1–3 |
| [docs/review-1-slides.md](docs/review-1-slides.md) | The 20-slide deck, mapped slide-by-slide to the marking scheme |
| [docs/demo.md](docs/demo.md) | The rehearsed 7-minute live demo, with a fallback for every failure mode |
| [docs/review-1-report.md](docs/review-1-report.md) | **The Review-I report** in the required VIT format — abstract to references |

---

## Status at a glance

| | |
|---|---|
| **Last updated** | 2026-08-23 |
| **Review-I** | ✅ **completed 19 Aug 2026** |
| **Project root** | `/Users/arushitiwari/Desktop/ShadowGuard` |
| **Git branch** | `main` (84 commits, working tree clean) — **nothing pushed to the remote yet** |
| **Current stage** | Stages 0–4 live: interception, regex, entropy, semantic, OCR, risk engine, redaction, persistence — for text **and** images, on ChatGPT and (unverified) Gemini. |
| **Completed** | **Weeks 1–7 complete.** Gemini adapter written but **not verified against the live site**. |
| **Next task** | **Week 8 — hardening, report, diagrams, demo.** Also open: close bypass B1, live-verify Gemini, improve OCR retention for `GOOGLE_API_KEY` and `JWT` (both 0%) |
| **Tests** | **316 Python** (`pytest -q`) · **67 JavaScript** (`npm test`) · extension **v0.11.2** |
| **Measured** | 230-sample corpus — text: recall **98.3%**, FPR **0%**, F1 **99.1%** (regex alone **75.7%**) · images: OCR retention **93.9%**, OCR-induced FPR **2.0%** |
| **Deadline** | ~2026-09-20 (8 weeks from 2026-07-26) |
| **Weeks elapsed** | **3.3 of 8**, with 6 weeks of plan delivered — well ahead |

### What actually runs today

Loading `extension/` unpacked into Chrome and opening `chatgpt.com` produces, in the page console:

```
 [ShadowGuard] ACTIVE            ← green badge
[ShadowGuard] version    : 0.11.2
[ShadowGuard] host       : chatgpt.com
[ShadowGuard] frame      : top-level
[ShadowGuard] adapter    : ChatGPT (chatgpt)
[ShadowGuard] composer matched at tier 1/6 via "#prompt-textarea"
[ShadowGuard] composer attached: <div id="prompt-textarea" …>
 [ShadowGuard] INTERCEPTING  keydown · click · submit — all send paths closed
[ShadowGuard] bridge OK — reply from service worker: {type: "SG_PONG", …}
```

**All three send paths are closed** — Enter, the send-button click, and form submission. Each logs
a red `SEND BLOCKED` group naming the trigger. Shift+Enter, IME composition, an empty composer,
clicks outside the send button, and Enter outside the composer all pass through untouched.
Switching conversations (SPA navigation, no page reload) logs `composer RE-attached`.

**The full loop runs against the real backend.** The extension holds the message, the service
worker `POST`s it to FastAPI on `127.0.0.1:8000`, and the returned verdict decides what happens.
A safe message replays silently and the user never knows ShadowGuard was there. A risky one raises
the Shadow-DOM modal: **Cancel** blocks and leaves the text for editing, **Send anyway** replays
it. At `block` severity there is no override button at all.

**Redaction is live.** When removing the findings would leave a usable message, the modal shows the
exact text that will be sent and offers **Send redacted**:

```
charge 4532015112830366 for the invoice please
        ↓
charge [REDACTED: CREDIT_CARD] for the invoice please
```

Nothing is rewritten without the user seeing it first — silently editing someone's message and
sending it would be a worse betrayal of trust than either warning or blocking. The placeholder
names the entity *type* and nothing else: a partial mask like `4532 **** **** 0366` is familiar
from banking UIs and wrong here, because the issuer prefix plus the last four digits identifies a
card in most customer-service systems.

Redaction is offered only when it actually **resolves** the risk. `our client Deborah Alvarez is
threatening to sue` gets a plain warning instead — a person is usually the *subject* of a sentence
rather than an incidental token in it, and redacting them leaves nothing to answer.

**The extension contains zero detection logic** — every verdict comes from Python. Stage 1 is live:

- **1a, regex** — 16 rules: vendor API keys, private keys, JWTs, Luhn-validated payment cards,
  IBAN, US SSN, Indian PAN and Aadhaar, email.
- **1b, entropy** — catches secrets of *unknown* format by measuring randomness, with thresholds
  derived from measurement rather than convention (see [D18](docs/decisions.md)).
- **Short-circuiting** — a `critical` regex hit skips entropy, and `stages_run` in the response
  proves it rather than claiming it.

Measured at **~0.04 ms** per message.

If the backend is unreachable, a **grey** "offline — not inspected" modal appears and sending is
the focused default (fail open, visibly — see [D12](docs/decisions.md)).

Try it, and note that the same secret produces different actions depending on how much of the
message it occupies:

| Send this | Verdict |
|---|---|
| `how do I sort a list in python` | **allow**, silently |
| `charge 4532015112830366 for the invoice please` | **redact** — score 54, the sentence survives |
| `charge 4111 1111 1111 1111` | **warn**, score 54 — the card is 73% of the message, so redacting leaves nothing to send |
| `charge 4111 1111 1111 1112` | **allow** — one digit changed, fails the Luhn checksum, so it is not a card |
| `AKIAIOSFODNN7EXAMPLE` | **block**, score 92 — the message *is* the secret |
| `my deploy key is AKIAIOSFODNN7EXAMPLE, how do I rotate it` | **redact** — same key, but now there is a question left to ask |
| `internal auth token Zq7Wm2Kp9Xr4Nv6Bt8Lc3Hd5Fj1Gy0Ae` | **warn**, score 43 — entropy alone, matching no pattern |
| `our client Deborah Alvarez is threatening to sue` | **warn**, score 40 — no identifier at all, caught by context |

Then stop the server and send anything to see the offline path.

Each request carries a `request_id` that appears in **both** the browser console and the uvicorn
terminal — that correlation is what makes an incident traceable across systems.

**Console handle for manual inspection** — switch the DevTools console context dropdown from `top`
to **ShadowGuard AI** first, or these are undefined:

```js
__sg.read()          // current composer text
__sg.isEmpty()       // ProseMirror-aware empty check
__sg.composer        // the live element
__sg.watcher.find()  // {element, selector, tier}
__sg.disable()       // stop blocking Enter, so the tab stays usable
__sg.enable()        // resume blocking
__sg.stats()         // {seen, intercepted, passed} — confirms guards fire
__sg.pending         // the held send, or null
__sg.approve()       // stands in for the modal's "Proceed"
__sg.discard()       // stands in for "Cancel"
__sg.sendRedacted(s) // swap the composer text, then send
__sg.tryReplace()    // diagnose the composer rewrite — reports selection coverage
__sg.clear()         // empty the composer
__sg.sendButton()    // where a replay would route right now
__sg.modal           // the modal instance (its shadow root is closed — this is the only handle)
```

Full testing guide, including the console-context trap and debugging by symptom:
**[docs/testing.md](docs/testing.md)**.

`console.debug` lines (the "why did this Enter pass through" messages) are hidden unless the
console filter is set to **Verbose**.

### Measured results — 200-sample corpus

```
  metric                regex only       stage 1   stage 1 + 2
  recall                    76.0%         89.0%         98.0%
  false-positive             0.0%          0.0%          0.0%
  precision                100.0%        100.0%        100.0%
  F1                        86.4%         94.2%         99.0%
  latency p95             0.021ms       0.022ms       3.630ms
```

**Entropy buys 13 points of recall, Stage 2 buys a further 9, and neither costs a single false
positive.**

That Stage 2 delta is the number Week 4's plan promised and could not produce. It exists now
because the **Week 5 risk engine** can act on `low`-severity findings; before it, semantic
detection was structurally incapable of changing any action-level metric
([D31](docs/decisions.md)).

> **Comparison with the v1 (150-sample) corpus**, kept verbatim rather than replaced, because the
> corpus changed between them and a number that moves for that reason must not be presented as
> progress:
>
> ```
>   metric                regex only       stage 1   stage 1 + 2
>   recall                    82.7%        100.0%        100.0%
>   F1                        90.5%        100.0%        100.0%
> ```
>
> v1 was **saturated** — zero misclassified samples, so no improvement could be measured at all
> ([D30](docs/decisions.md)). v2 adds 50 samples written to a
> [specification committed before any of them existed](evaluation/corpus-spec-v2.md), with six
> predictions recorded in advance. **Three of the six were wrong**, and the grading is in §7 of
> that document.

#### How the risk engine found those 9 points

Before Week 5, eleven sensitive messages were allowed through. Look at what happened to one:

```
FALSE NEGATIVE  S076   "draft a termination letter for Marcus Delgado, our
                        regional sales lead at Brightpath Logistics..."
  expected:  PERSON, ORGANIZATION
  found:     ORGANIZATION, PERSON      ← detection was perfect
  action:    allow                     ← and it was allowed anyway
```

**8 of the 11 false negatives had completely correct detection.** Every entity was found; the
message went through because `PERSON` and `ORGANIZATION` are `low` severity, and `low` means
`allow` ([D27](docs/decisions.md)).

**Detection was never the limiter — policy was.** 11 points of recall were recoverable without
changing a single detector.

The obvious fix is wrong, and measuring it *before* building anything is what kept it out. Warning
on two-or-more findings catches 8 of the 11 **and costs 13 benign false positives**, including
`explain how Satya Nadella restructured Microsoft`. Counting findings is not fusion; those messages
contain the same entity types as the ones that should be stopped.

What separates them is whether the person is a **data subject** or a **topic**:

| | |
|---|---|
| `our client Deborah Alvarez is threatening to sue…` | **warn**, score 40 |
| `explain how Satya Nadella restructured Microsoft` | **allow**, score 20 |

Same entity types, same severities, opposite verdicts — decided by the relationship language around
the name. This is [D29](docs/decisions.md)'s *used vs discussed* distinction applied one level up,
to fusion instead of detection. Reasoning in [D33](docs/decisions.md); every weight and threshold
lives in [`backend/app/core/policy.py`](backend/app/core/policy.py), so a policy change is not a
code change.

The engine also returns **why**, in plain language — `"a name together with a way to contact them"`
— because a score nobody can explain is a score nobody will trust.

**Two false negatives remain, both honest:**

- `S081` *"we are acquiring Silverbrook Analytics next quarter"* — material non-public information
  with no person in it. **Confidential business information is a category this system does not
  model**, and no PII tuning would reach it.
- `S098` — spaCy labelled `Meenakshi Balasubramanian` as `NRP` rather than `PERSON`, so the
  identity + contact rule never fired. The non-Western-name weakness below, now costing a real
  miss rather than only an entity-level score.

#### Entity-level metrics — where Stage 2 finally becomes visible

Every action-level number answers *"should this message have been stopped?"*. Semantic findings
are `low` severity and therefore always resolve to `allow`, so **Stage 2 cannot move any of them,
no matter how many samples are added**. It is measured per entity type instead:

| type | n | recall | precision |
|---|---|---|---|
| `PERSON` | 29 | 79.3% | 69.7% |
| `ORGANIZATION` | 21 | 85.7% | **51.4%** |
| `LOCATION` | 7 | **42.9%** | **37.5%** |
| `CREDIT_CARD`, `US_SSN`, `IBAN`, all vendor keys | — | 100% | 100% |
| `HIGH_ENTROPY_STRING` | 15 | 86.7% | 100% |
| `EMAIL` | 11 | 100% | 84.6% |

`LOCATION` finds under half of what is present and is wrong about two-thirds of what it claims —
the weakest component in the system, and a candidate for removal. `ORGANIZATION` precision of
51.4% means **half of everything it reports is wrong**; harmless at `low` severity, but any Week 5
fusion rule that treats it as evidence inherits a coin flip.

This view also caught two defects that were invisible while only actions were measured:
`HIGH_ENTROPY_STRING` recall is 86.7%, not 100%, and `EMAIL` precision is 84.6%.

#### A measured ethical finding

`PERSON` recall is **70.0%** on Western names and **57.1%** on the Indian, Chinese, Arabic,
Vietnamese and West African names of group G3.

spaCy's English models are trained predominantly on Western news text, and this is the
consequence: **the tool protects some people measurably worse than others.** It is reported as a
result, not a caveat.

The layering partially compensates, which nobody designed and which is worth saying: the G3
samples that *were* caught were caught by `INDIA_PAN`, `INDIA_AADHAAR` and `CREDIT_CARD` — Stage 1
rules written deliberately for a non-US context.

#### Context requirements (4.2)

The two remaining false positives were cleared by gating the two weakest rules — `INDIA_AADHAAR`
and `INDIA_PAN` — on a supporting word appearing within 50 characters. See
[D29](docs/decisions.md).

The clearest illustration this project has produced, and it came out of the corpus rather than
being constructed:

| Text | Verdict |
|---|---|
| `the vendor submitted PAN ABCDE1234F for gst registration` | **sensitive** |
| `our sku format is ABCDE1234F which collides with the validator` | **benign** |

The same ten characters. The difference is whether the number is being **used** or **discussed**,
and that evidence is not in the token at all.

This trades recall for precision, and the cost is real: `my number is 2345 6789 0123` carries no
supporting word and is now missed. Accepted for rules this weak; recorded as a limitation.

Reproduce with:

```bash
python -m evaluation.run_eval --failures
```

> **On base rates:** precision measured on a balanced corpus overstates real precision, because
> real traffic is perhaps 1% sensitive. Both figures are reported. See
> [evaluation/README.md](evaluation/README.md) — this is the base-rate fallacy, and it is the most
> common way security-tool evaluations mislead. It reads 100% here only because the measured
> false-positive rate is zero; at the interval's upper bound of 4.9% it would fall to about 17%.
> That gap between 100% and 17%, produced by a difference well inside the corpus's resolution, is
> the single best argument for Task 4.3.

---

## Locked decisions

These were decided on 2026-07-26 and should **not** be re-litigated without a deliberate change of plan.

| Decision | Choice | Rationale |
|---|---|---|
| **Interception model** | **Block before send** — cancel the Enter/send-click, hold the message, call the backend, show a modal, and replay the send only on approval | This is what makes the "Prevention" in DLP true. Observe-and-warn would only report leaks after they happened. |
| **Platform scope** | **ChatGPT + Gemini**, via a pluggable per-site adapter layer | ChatGPT first; Gemini added in Week 6 as a second adapter so a fragile DOM integration never blocks backend work. |
| **Timeline** | ~8 weeks from 2026-07-26 | Target ~2026-09-20. |
| **Graded on** | Demo + written report + **quantitative evaluation** | Precision/recall, false-positive rate, latency, benchmarked against regex-only DLP. This makes the labelled corpus and evaluation harness first-class deliverables, built in Week 3 — not bolted on at the end. |
| **Extension build** | Plain JS, no bundler | A bundler puts a build step between the code you write and the code Chrome runs, so console line numbers stop matching your files. Not worth it — the extension needs no npm packages. |
| **Backend network access** | Assumed **fully local**, no external API calls | A DLP tool that ships your secrets to a third party is self-defeating. *(Confirm if this changes.)* |

### Still open — to be asked at the right time

| Question | When it gets decided |
|---|---|
| Store uploaded images, or only extracted OCR text? | Week 6 — a real privacy question, worth a paragraph in the report |
| Local-only backend vs. deployed | Week 8 |

---

## Architecture

The single most important idea: **the browser detects and enforces, the server decides.**
The extension is deliberately dumb; all intelligence lives in Python. This is how real DLP
products are built — detection logic can be updated without redeploying the extension to
every employee.

```
┌─────────────────── CHROME (tab open on chatgpt.com) ────────────────────────┐
│                                                                             │
│   ChatGPT's own React page  ── not our code, we are a guest here            │
│         ▲  user types / pastes / uploads image                              │
│         │                                                                   │
│   ┌─────┴───────────────┐   injects   ┌──────────────────────────┐          │
│   │   CONTENT SCRIPT    │────────────►│  WARNING MODAL (our UI)  │          │
│   │  (runs in the page) │◄────────────│  [ Cancel ] [ Proceed ]  │          │
│   └─────┬───────────────┘   verdict   └──────────────────────────┘          │
│         │                                                                   │
│         │  chrome.runtime.sendMessage()   ← extension-internal messaging    │
│         ▼                                                                   │
│   ┌─────────────────────┐                                                   │
│   │   SERVICE WORKER    │  the ONLY component allowed to make HTTP calls    │
│   │    (background)     │                                                   │
│   └─────┬───────────────┘                                                   │
└─────────┼───────────────────────────────────────────────────────────────────┘
          │
          │  POST /api/v1/analyze
          │  { text, image_b64?, user_id, platform, timestamp }
          ▼
┌─────────────────── FASTAPI BACKEND (localhost:8000) ────────────────────────┐
│                                                                             │
│   route handler ──► PIPELINE ORCHESTRATOR                                   │
│                          │                                                  │
│    ┌─────────────────────┼──────────────┬──────────────┐                    │
│    ▼                     ▼              ▼              ▼                    │
│  STAGE 1              STAGE 2        STAGE 3       STAGE 4                  │
│  regex + entropy      Presidio       Tesseract     behavioral               │
│  ~1ms, catches        semantic NER   OCR on        rolling baseline         │
│  API keys, cards      names, orgs,   images        per user                 │
│    │                  locations         │              │                    │
│    │                     ▲              │              │                    │
│    │                     └──────────────┘              │                    │
│    │              OCR text re-enters Stage 1 & 2       │                    │
│    └─────────────────────┴──────────────┬──────────────┘                    │
│                                         ▼                                   │
│                                  STAGE 5: RISK ENGINE                       │
│                                  weighted fusion → score 0-100              │
│                                         │                                   │
│                        ┌────────────────┴─────────────────┐                 │
│                        ▼                                  ▼                 │
│              verdict: allow / warn /              ┌────────────────┐        │
│              redact / block  ──────────────►      │     SQLITE     │        │
│                        │                          │ events         │        │
│                        │                          │ user_profiles  │        │
│                        │                          └───────┬────────┘        │
└────────────────────────┼──────────────────────────────────┼─────────────────┘
                         │ JSON response back to extension  │ read-only
                         ▼                                  ▼
                (modal shown to user)            ┌─────────────────┐
                                                 │ ADMIN DASHBOARD │
                                                 └─────────────────┘
```

### The three JavaScript worlds

Extension code does not run in one place. It runs in three worlds that **cannot see each other's
variables**. This isolation is Chrome's security model, and it dictates our whole design.

| World | What it is | Can | Cannot |
|---|---|---|---|
| **1 — The page** | ChatGPT's own React code | — | *(not our code)* |
| **2 — Content script** | Our JS, injected into the tab | Read/modify the DOM, attach listeners, render our modal | See the page's JS variables; make cross-origin HTTP calls |
| **3 — Service worker** | Our JS, one instance for the whole extension | Full `chrome.*` API, cross-origin `fetch()` | Touch the DOM. Also **dies after ~30s idle** |

Worlds 2 and 3 communicate **only** by passing JSON messages.

Two consequences that cause real bugs later:

1. The content script must intercept the Enter key (it needs the DOM), but only the service worker
   can call FastAPI (it needs the network). Neither can do the job alone.
2. **Never store state in a service-worker global.** Chrome kills the worker after ~30 seconds idle
   and every global is lost. Anything that must survive goes in `chrome.storage`.

### Request trace — pasting an API key

1. Content script's **capture-phase** `keydown` listener fires *before* ChatGPT's own handler and calls `preventDefault()` — the message does not send.
2. Content script reads the composer text, sends it to the service worker.
3. Service worker `fetch()`es `POST localhost:8000/api/v1/analyze`.
4. Stage 1 regex matches the OpenAI key pattern → entropy confirms high randomness → CRITICAL. Because Stage 1 already found a critical secret, the orchestrator short-circuits and skips Stage 2 (~50ms of spaCy saved).
5. Stage 4 raises risk if this is the 5th such attempt today. Stage 5 fuses → score 95 → BLOCK.
6. Event row written to SQLite. JSON returned: `{score: 95, action: "block", entities: [...]}`.
7. Service worker relays to content script → modal renders → user clicks Cancel, or (if `warn`) Proceed, which sets a bypass flag and re-dispatches the Enter key.

Steps 1 and 7 are the hardest engineering in the project. **You cannot "pause" a keystroke in
JavaScript** — you can only cancel it and later replay it. Everything about Stage 0's design
follows from that.

---

## Folder structure

```
ShadowGuard/
├── README.md                   ← this file; session-recovery document
├── .gitignore
│
├── extension/                  ← Stage 0. Everything that runs in Chrome.
│   ├── manifest.json           the extension's contract with Chrome: permissions,
│   │                           which scripts run where. Chrome reads this first.
│   ├── src/
│   │   ├── content/            scripts injected INTO chatgpt.com's page. Touch the DOM,
│   │   │                       sandboxed from the page's JS, no cross-origin HTTP.
│   │   ├── background/         the MV3 service worker. Event-driven, no DOM, dies after
│   │   │                       ~30s idle. Owns ALL network calls to our API.
│   │   ├── adapters/           one file per platform. Each knows only "where is the
│   │   │                       composer / send button / how do I read and send". This is
│   │   │                       why adding Gemini in Week 6 is one new file, not a rewrite.
│   │   ├── ui/                 the warning modal, in a CLOSED Shadow DOM so ChatGPT's CSS
│   │   │                       cannot break our styling, or the page reach our buttons.
│   │   └── shared/             message-type constants used by both worlds — prevents
│   │                           typo'd message names failing silently.
│   ├── tests/                  JSDOM tests. Every regression verified by re-introducing
│   │                           its bug — see extension/tests/README.md.
│   └── assets/icons/           toolbar icons (16/48/128px).  [empty — Week 8 polish]
│
├── backend/                    ← Stages 1-5. All the intelligence.
│   ├── app/
│   │   ├── main.py             FastAPI app, CORS, lifespan (warms spaCy, opens the store).
│   │   ├── api/routes/
│   │   │   ├── analyze.py      HTTP only — parse, call pipeline, log, persist, respond.
│   │   │   └── dashboard.py    The admin page. Read-only: every non-GET returns 405.
│   │   ├── schemas/
│   │   │   └── analyze.py      Pydantic models = the typed contract. THIS is the API spec.
│   │   ├── detectors/          Pure functions. No I/O, no HTTP — which is what lets the
│   │   │   │                   evaluation harness call them hundreds of times per second.
│   │   │   ├── base.py         Finding, overlap resolution, severity ordering.
│   │   │   ├── regex_detector.py     Stage 1a — 20+ rules, Luhn, context gates (D29).
│   │   │   ├── entropy_detector.py   Stage 1b — Shannon entropy for unknown formats.
│   │   │   ├── semantic_detector.py  Stage 2 — Presidio + spaCy, entity allowlist (D28).
│   │   │   └── catalog.py      entity type → severity, assembled FROM the detectors.
│   │   ├── pipeline/
│   │   │   └── orchestrator.py Stage sequencing, short-circuiting, Stage-2 merge (D26).
│   │   ├── core/
│   │   │   ├── policy.py       EVERY tunable weight and threshold. Data, not code.
│   │   │   ├── risk.py         Weighted fusion (D33). The arithmetic only.
│   │   │   ├── redactor.py     Typed placeholders + coverage (D36, D37).
│   │   │   └── behaviour.py    Stage 4 — a multiplier on the score, never a finding (D49).
│   │   ├── models/
│   │   │   └── event.py        SQLAlchemy tables. No column can hold a prompt.
│   │   ├── db/
│   │   │   ├── session.py      Lazy engine, SQLite pragmas, transactional scope.
│   │   │   └── events.py       The ONLY module that writes. Dashboard queries live here.
│   │   └── templates/
│   │       └── dashboard.html  The admin UI, server-rendered by this same app (D50).
│   │                           No chart library: bars are divs. A CDN request would
│   │                           break the no-external-calls property, invisibly.
│   └── tests/                  pytest suite, incl. conftest.py isolating the database.
│
├── evaluation/                 ← your grade depends on this folder
│   ├── data/corpus.jsonl       200 labelled samples, 100 sensitive / 100 benign.
│   ├── corpus.py               loader + the honesty rules that keep labels consistent.
│   ├── metrics.py              Wilson intervals, base-rate correction, entity-level counts.
│   ├── run_eval.py             action-level AND entity-level metrics, three configurations.
│   └── corpus-spec-v2.md       the PRE-REGISTERED extension spec, then graded (D32).
│
├── docs/                       ← the knowledge base. Read docs/README.md first.
├── data/                       ← the SQLite decision store  [gitignored — local user data]
└── scripts/                    ← setup helpers  [empty]
```

**The one structural rule that matters:** detection logic lives in `detectors/`, never in
`api/routes/`. Routes handle HTTP; detectors handle detection. Blur that line and the evaluation
harness can't call detectors without spinning up a web server — tests become slow and flaky, and
the project becomes unmeasurable.

---

## Current source files

| File | Lines | Purpose |
|---|---|---|
Content scripts load in the order listed in the manifest; each file depends on the ones above it.

| File | Purpose |
|---|---|
| `extension/manifest.json` | MV3 declaration: version, service-worker registration, content-script injection rules for `chatgpt.com` and `chat.openai.com` |
| `extension/src/shared/messages.js` | Frozen `SG.MSG` constants — the shared message vocabulary. Loaded by **both** worlds (manifest for content, `importScripts` for worker) |
| `extension/src/adapters/registry.js` | `registerAdapter()` / `resolveAdapter()`. Exact-hostname matching, not substring — substring matching is how extensions end up running on `chatgpt.evil-site.com` |
| `extension/src/adapters/chatgpt.js` | **The only file that knows how ChatGPT is built.** Tiered composer/send-button selectors, `<textarea>` vs contenteditable text extraction, ProseMirror empty-state handling |
| `extension/src/content/composer_watcher.js` | `MutationObserver` that discovers the composer and re-discovers it when SPA navigation detaches it. Reports selector-tier degradation |
| `extension/src/content/interceptor.js` | Capture-phase `keydown` / `click` / `submit` listeners on `window` that cancel a send, plus the shared guards that decide when not to |
| `extension/src/ui/modal.js` | The warning dialog. Closed Shadow DOM for style isolation, `<dialog>.showModal()` for top-layer rendering and free focus trapping. `show()` returns a promise resolving to the user's decision |
| `extension/src/content/content.js` | World 2 orchestration only — no site knowledge, no discovery logic. Re-injection guard, adapter selection, debounced read instrumentation, `__sg` debug handle, PING across the bridge |
| `extension/src/background/service_worker.js` | World 3. Lifecycle logging and the message router; replies PONG to PING |

### Design decisions worth being able to defend

1. **`innerText`, not `textContent`.** ProseMirror renders one `<p>` per visual line.
   `textContent` ignores layout, so `<p>sk-proj-abc</p><p>def123</p>` becomes
   `"sk-proj-abcdef123"` — two lines fused into something that looks exactly like one API key.
   `innerText` yields `"sk-proj-abc\ndef123"`. This is a **detection-accuracy** decision, not
   formatting.
2. **Tiered selectors with tier logging.** IDs and `data-testid` attributes are written by humans
   for their own CI, so they rarely change; CSS class names are build-generated and get hashed on
   any refactor. A drop from tier 1 to tier 3 prints a warning *before* anything breaks. Silent
   degradation is the failure mode to avoid — a DLP tool that quietly stops detecting is worse
   than none, because people still trust it.
3. **`element.isConnected` guard.** The watcher callback runs on *every* DOM mutation, including
   every token streamed into an assistant reply. The guard makes the common case one property
   read. `offsetParent !== null` additionally filters hidden scaffolding ChatGPT leaves in the DOM.
4. **Capture phase on `window`, not the composer.** A keydown travels window → … → composer
   (capture), fires on the composer (target), then bubbles back up. `addEventListener` defaults to
   **bubble**, which is too late — ChatGPT already sent. Attaching *capture* to the composer itself
   is also insufficient: at the target element, capture and bubble listeners fire in **registration
   order**, and ChatGPT registered first. Capture on an ancestor is the only placement the DOM spec
   guarantees runs first.
5. **Cancellation needs both `stopImmediatePropagation()` and `preventDefault()`.** ChatGPT sending
   is *their JavaScript handler*, not a browser default action — so `preventDefault()` alone does
   not stop it. Conversely `stopImmediatePropagation()` alone blocks the send but lets the browser
   insert a newline into the box. Each omission is a distinct visible bug.
6. **`event.isTrusted` instead of a bypass flag.** Synthetic events dispatched from JS are always
   `isTrusted: false` and it cannot be forged, so Task 2.3's approved-send replay passes through
   automatically — no "am I replaying?" boolean to leave stuck in the wrong state after an
   exception.
7. **IME guard (`isComposing` / `keyCode === 229`).** For Japanese, Chinese and Korean input, Enter
   *confirms the IME candidate* rather than sending. Swallowing it would make ShadowGuard unusable
   for a large share of the world's users — an internationalisation correctness issue, not a nicety.
8. **`closest()`, never `matches()`, for the send button.** The arrow is a `<button>` wrapping an
   `<svg>` wrapping a `<path>`, and the browser reports the innermost element under the cursor — so
   `event.target.matches(buttonSelector)` is false essentially always and fails *silently*.
   `closest()` walks up the ancestors and finds the button however deep you clicked.
9. **Event delegation over stored references.** Nothing holds a reference to the send button,
   because the platform remounts it constantly (it swaps to a *stop* button while a reply streams).
   We ask "did this click originate inside a send button?" per event, which is immune to remounting
   by construction — the same reasoning as the composer watcher, applied to a shorter-lived element.
10. **Three send paths, one guard set.** `keydown`, `click` and `submit` all route through
   `#sharedGuards()`. Duplicating guards across three handlers is how one drifts out of sync,
   producing a bug that only reproduces when the user sends one particular way.
11. **Replay re-triggers the send; it does not rebuild the message.** We cancelled the send *event*,
   never the text — it is still in the composer and React's state already holds it, because React
   saw every keystroke. This is why the well-known native-value-setter workaround for React
   controlled inputs is *not* needed here. It belongs to Week 5's `redact` action, the only case
   that rewrites the user's text.
12. **Replay clicks the platform's own send button.** Everything ChatGPT does on send — clearing the
   box, optimistic UI, scrolling, analytics — then happens exactly as it normally would. Falls back
   to a synthetic bubbling Enter only when the button is missing, disabled, or has been swapped for
   the *stop* control mid-stream (clicking that would abort the reply instead of sending).
13. **No auto-retry on a failed replay.** Post-send verification warns if the composer did not
   clear, but never resends. Retrying a failed action is good practice in most apps; in a DLP tool
   it means the message you were *already unsure about* gets sent twice.

### Permissions currently requested: **none**

`manifest.json` declares no `permissions` and no `host_permissions`. This is deliberate —
**principle of least privilege**: every permission is a liability, not a feature. They get added
one at a time, at the moment they are needed:

| Permission | What it buys | When | Cost |
|---|---|---|---|
| `permissions: ["storage"]` | `chrome.storage`, for surviving worker death | Week 2 | Low, no scary warning |
| `host_permissions` | Lets the service worker `fetch()` our backend | Week 3 | Install-time warning to the user |
| `web_accessible_resources` | Lets page-context code load our files | only if needed | Widens attack surface — avoid |

Notably absent: `<all_urls>`, `tabs`, `scripting`, `webRequest`. "We request the minimum
permissions necessary and add them incrementally" is a real security argument that the git
history backs up.

---

## 8-week roadmap

Every task is independently testable — you know it works before moving on.

### Week 1 — Stage 0a: get code running inside ChatGPT

| Task | Test | Status |
|---|---|---|
| 1.1 Repo + folder skeleton, `git init` | `git log` shows the first commit | ✅ |
| 1.2 `manifest.json`, load unpacked | Extension appears in `chrome://extensions` with no errors | ✅ |
| 1.3 Content script logs "ShadowGuard active" | Visible in the ChatGPT page's DevTools console | ✅ |
| 1.4 Service worker receives a message | Two consoles, both logging — proves the bridge | ✅ |
| 1.5 Locate ChatGPT's composer element reliably | You can read the typed text from the console | ✅ |

### Week 2 — Stage 0b: the interception itself (hardest week)

| Task | Test | Status |
|---|---|---|
| 2.1 Capture-phase `keydown` listener cancels Enter | Press Enter, nothing sends, console logs the text | ✅ |
| 2.2 Intercept the send-button click path | Clicking the arrow is also blocked | ✅ |
| 2.3 Replay the send programmatically after approval | Message sends after a fake "approve" | ✅ |
| 2.4 Inject the modal UI (Shadow DOM) | Modal renders correctly over the page | ✅ |
| 2.5 Wire modal → Cancel discards, Proceed sends | Full loop works with a hardcoded verdict | ✅ |
| 2.6 Paste and image-upload interception | Console shows pasted text and file bytes | ✅ **closed in 6.2** |

> **Why 2.6 is deferred.** Pasted text needs no special handling: the interceptor reads the
> composer at *send* time, so it sees pasted content exactly as it sees typed content — already
> covered and verified. The remaining half is capturing uploaded *images*, which is dead weight
> until Tesseract exists. It is better built in Week 6, next to the OCR code that consumes it.

### Week 3 — Stage 1 + evaluation foundation

| Task | Test | Status |
|---|---|---|
| 3.1 Python 3.12 + venv + `fastapi uvicorn` | `/docs` opens in the browser | ✅ |
| 3.2 `POST /api/v1/analyze` returning a stub verdict | `curl` gets JSON back | ✅ |
| 3.3 Connect extension → backend end-to-end | Real modal driven by a real HTTP response ← **first true milestone** | ✅ |
| 3.4 Regex detector module (API keys, cards, emails, IBAN, JWT) | Unit tests pass on positive + negative cases | ✅ 73 tests |
| 3.5 Shannon entropy scorer | High on random keys, low on English prose | ✅ 101 tests |
| 3.6 Labelled test corpus (~150 samples) | A rerunnable labelled dataset | ✅ 150 samples, JSONL |
| 3.7 Evaluation harness (precision / recall / FPR) | A printed metrics table | ✅ see results below |

### Week 4 — Stage 2: semantic analysis

| Task | Test | Status |
|---|---|---|
| 4.1 Presidio + spaCy, allowlisted entities, merged without double-counting | Semantic labels appear; no secret is suppressed by one | ✅ |
| 4.2 Context requirements for the two weakest rules | Same token flagged when used, ignored when discussed | ✅ |
| 4.3a Entity-level metrics + entity registry | Per-type recall/precision printed | ✅ |
| 4.3b Corpus extension to 200 samples, pre-registered | Predictions graded, three of six wrong | ✅ |

**Week 4 complete.**

> **The plan changed, and the reason is worth recording.** Week 4 was written to end with "the
> false-positive drop vs. Week 3 — the single most important number in the report." **That number
> does not exist**, because 4.2 took the false-positive rate to zero on regex alone, leaving Stage
> 2 no false positives to remove.
>
> The deeper reason emerged in 4.3 and is structural rather than incidental: the corpus labels the
> **action** ([D21](docs/decisions.md)) and semantic findings are `low` severity and therefore
> always `allow` ([D27](docs/decisions.md)), so **Stage 2 can never change an action-level metric**
> under the current policy. No quantity of new samples could have produced the promised delta.
>
> Meanwhile `Sample.expected` had been loaded and ignored by `metrics.py` since Week 3 —
> entity-level evaluation simply did not exist. 4.3a built it, and Stage 2 became measurable
> immediately. See **[D31](docs/decisions.md)** and
> **[the pre-registered specification](evaluation/corpus-spec-v2.md)**.

### Week 5 — Risk engine + persistence

| Task | Test | Status |
|---|---|---|
| 5.1 Risk engine: weighted fusion, tunable policy file | Recall 89% → 98% at 0% FPR | ✅ |
| 5.2 Redaction: `redact` action and `redacted_text` | Secret removed, rest of the message survives | ✅ |
| 5.3 Extension test harness (JSDOM) | Each regression verified by re-introducing its bug | ✅ |
| 5.4 SQLAlchemy models + SQLite schema | Rows written; the database file itself is searched for secrets | ✅ |
| 5.5 Event logging and dashboard-ready queries | Decisions queryable by user, action and entity type | ✅ |

> **5.1 delivered the number Week 4 could not.** The Stage 1 → Stage 1+2 gap the original plan
> called "the single most important number in the report" is **+9 recall points**, and it only
> became measurable once the engine could act on `low`-severity findings. See
> [D33](docs/decisions.md).
>
> **One decision is still open for 5.2:** whether `redact` edits the composer in place or requires
> the user to re-confirm the edited text. `redact_threshold` is deliberately `None` until then —
> emitting an action the extension cannot perform would leave a message neither sent nor blocked,
> silently.

### Week 6 — Stage 3 (OCR) + Gemini adapter
Tesseract · image preprocessing (where OCR accuracy is won or lost) · feeding OCR text back
through Stages 1–2 · then the Gemini site adapter.

### Week 7 — Stage 4 + dashboard ✅
Rolling behavioural baseline (z-scores over a user's own history, D49) · dashboard UI · charts.
Server-rendered on the existing FastAPI app rather than a second process (D50).

### Week 8 — Hardening, report, demo
Final evaluation run · README polish · architecture diagrams · limitations & ethics · rehearsed
demo script · presentation.

**Contingency:** if behind at Week 6, the thing to drop is **Gemini**, documented in the report as
designed-but-unimplemented. Dropping the evaluation is not an option.

---

## Setup & running

### Load the extension (works today)

1. Open Chrome → `chrome://extensions`
2. Toggle **Developer mode** ON (top-right)
3. Click **Load unpacked**
4. Select `/Users/arushitiwari/Desktop/ShadowGuard/extension`
5. Open `https://chatgpt.com`, then DevTools (⌥⌘I) → **Console** → look for the green
   `[ShadowGuard] ACTIVE` badge
6. For service-worker logs: back on `chrome://extensions`, click **service worker** on the
   ShadowGuard card — it opens a *separate* DevTools window. Worker logs never appear in the
   page console.

> Chrome remembers the **absolute path** of an unpacked extension. Moving the project folder
> means re-loading the extension.

**After editing extension code:** click the ↻ reload icon on the ShadowGuard card, *then* reload
the ChatGPT tab. Editing the service worker requires the extension reload; editing the content
script requires the page reload. Skipping either is the most common "my change did nothing"
confusion — and the version line in the console (`[ShadowGuard] version : 0.9.3`) is how you tell
whether the reload actually took.

### Extension tests

```bash
npm install     # once — pulls jsdom
npm test        # the extension suite
```

Runs against JSDOM in about a second, and covers the composer rewrite, the interception guards, the
modal's decision flow, and the Python ↔ JavaScript action vocabulary. It does **not** replace
opening Chrome — see [extension/tests/README.md](extension/tests/README.md) for what it
deliberately cannot reach.

### Backend

System Python is **3.9.6 (Apple's)** and must not be used: `pip install` into it can break OS
tooling, and 3.9 sits at the bottom of the supported range for Presidio/spaCy, which causes
dependency pain unrelated to this project.

```bash
brew install python@3.12
cd /Users/arushitiwari/Desktop/ShadowGuard
python3.12 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn
uvicorn backend.app.main:app --reload
```

With the server up, the admin dashboard is at **http://127.0.0.1:8000/dashboard** — every decision
the pipeline has made, and no message content, because the schema has nowhere to put any (D50).

### Toolchain status

| Tool | State | Notes |
|---|---|---|
| Node 22.18 / npm 10.9 | ✅ | Runs the extension's test suite (`npm test`) |
| Chrome | ✅ | |
| Homebrew | ✅ | |
| git 2.50 | ✅ | Committing as `tarushi1 <arushitiwari1503@gmail.com>` |
| Python 3.12 | ❌ | **Week 3** — `brew install python@3.12` |
| Tesseract | ❌ | **Week 6** — `brew install tesseract` |

---

## Security, privacy & ethics notes

To be expanded for the report; recording as they come up:

- **The `.gitignore` matters more than usual here.** The SQLite DB will contain real logged
  prompts, and screenshots captured during OCR testing may contain real secrets. Both are
  gitignored. A leaked `.env` in a DLP project is an embarrassing story.
- **Least-privilege permissions** — see the table above. Justifiable via git history.
- **`sender` is Chrome-populated, not content-script-supplied**, so it cannot be spoofed by a
  malicious page. That makes it the trustworthy source for which tab/URL a request came from —
  which matters once events are attributed to users.
- **Employee monitoring is surveillance.** The dashboard logs what people typed. The report
  needs an honest section on proportionality, retention, and consent.
- **False negatives are the security failure; false positives are the adoption failure.** A tool
  that cries wolf gets disabled. This is precisely what the Week 3 evaluation harness measures.

### Known bypasses — track these, they belong in the report

| Bypass | Status |
|---|---|
| **Edit-after-analysis.** The composer stays editable while a decision is pending, so a user can trip the warning with benign text, paste a secret while the modal is open, then click Proceed — sending text that was never inspected. | Detected and warned in `approve()`; becomes re-analysis in Week 5 |
| **`__sg.disable()`** is reachable from the page's own DevTools. Acceptable for a local prototype; in a real deployment the console handle would not ship. | By design, dev-only |
| **Anything not going through the UI** — the ChatGPT API, a different client, a browser without the extension. The extension is a control on one path, not a perimeter. | Inherent to the approach; state it plainly |

---

## Git history

```
393e9ae  docs: add knowledge base under docs/
c45de5f  feat: connect the extension to the FastAPI backend
61469ec  feat(backend): POST /api/v1/analyze with typed contract and tests
3d3c7e1  feat(backend): FastAPI app skeleton with health endpoint
f2c7445  docs: close out Week 2 in README; defer 2.6 to Week 6
cfeb89a  feat(extension): wire the modal to the send decision
f0a6099  feat(extension): warning modal in a closed Shadow DOM
ae553d3  fix(extension): name the three composer-attach cases distinctly
5761587  fix(extension): upgrade to a better composer match when one mounts
a6b7617  docs: update README for Task 2.3 (approved-send replay)
9a85fcc  feat(extension): replay an approved send
420079b  docs: update README for Task 2.2 (send-button interception)
fcc1c18  feat(extension): close the send-button and form-submit paths
ca9f092  docs: correct version string in README console sample
858b929  docs: update README for Task 2.1 (Enter interception)
cbe13bb  feat(extension): cancel Enter before ChatGPT can send
1ae1803  docs: update README for Task 1.5 (composer discovery)
d895312  feat(extension): locate ChatGPT composer via adapter + MutationObserver
552ae37  docs: sync README git-history section
1941d40  chore: ignore local editor and tool state
dc124df  docs: add README as living session-recovery document
06d0ca3  feat(extension): add service worker and content<->worker message bridge
17a6640  feat(extension): inject content script into ChatGPT
9214afa  feat(extension): add minimal Manifest V3 declaration
f882ec8  chore: initialise ShadowGuard AI project skeleton
```
