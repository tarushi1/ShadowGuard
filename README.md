# ShadowGuard AI

**A Context-Aware, Multimodal Data Loss Prevention Framework for Enterprise Interactions with Generative AI Platforms.**

ShadowGuard sits between an employee and a browser-based generative-AI platform (ChatGPT, Gemini)
and inspects everything — typed text, pasted text, uploaded images — *before* it is sent.
It computes a risk score and decides whether to **allow**, **warn**, **redact**, or **block**.

> **This README is the session-recovery document.** If a session expires or context is lost, read
> this file first: it records the current state, every locked decision, and the exact next task.
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

---

## Status at a glance

| | |
|---|---|
| **Last updated** | 2026-08-01 |
| **Project root** | `/Users/arushitiwari/Desktop/ShadowGuard` |
| **Git branch** | `main` (40 commits, working tree clean) |
| **Current stage** | Stages 0, 1 and 2 running end to end. Week 4 in progress. |
| **Completed** | **Weeks 1–3 complete**; 4.1 Presidio, 4.2 context requirements. 2.6 deferred to Week 6. |
| **Next task** | **4.3 — extend the corpus; it is saturated (0 failures, see D30)** |
| **Deadline** | ~2026-09-20 (8 weeks from 2026-07-26) |
| **Weeks elapsed** | ~0.6 of 8 — **well ahead of plan** |

### What actually runs today

Loading `extension/` unpacked into Chrome and opening `chatgpt.com` produces, in the page console:

```
 [ShadowGuard] ACTIVE            ← green badge
[ShadowGuard] version    : 0.8.0
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

Try it: `how do I sort a list in python` sends straight through; `charge 4111 1111 1111 1111`
warns (Luhn-validated — change the last digit and it stops warning); `deploy key
AKIAIOSFODNN7EXAMPLE` blocks; `internal auth token Zq7Wm2Kp9Xr4Nv6Bt8Lc3Hd5Fj1Gy0Ae` warns on
entropy alone, matching no pattern. Then stop the server and send anything to see the offline path.

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
__sg.sendButton()    // where a replay would route right now
__sg.modal           // the modal instance (its shadow root is closed — this is the only handle)
```

Full testing guide, including the console-context trap and debugging by symptom:
**[docs/testing.md](docs/testing.md)**.

`console.debug` lines (the "why did this Enter pass through" messages) are hidden unless the
console filter is set to **Verbose**.

### Measured results — Stage 1, 150-sample corpus

```
  metric                regex only       stage 1   stage 1 + 2
  recall                    82.7%        100.0%        100.0%
  false-positive             0.0%          0.0%          0.0%
  precision                100.0%        100.0%        100.0%
  precision @ 1%           100.0%        100.0%        100.0%
  F1                        90.5%        100.0%        100.0%
  latency p95             0.018ms       0.019ms       3.490ms
```

**Entropy buys 17 points of recall at no false-positive cost.** That is the one clean,
well-supported claim in this table.

#### Read the 100% as a warning, not a result

**This corpus is saturated: every detector improvement now has nothing left to measure.**
A perfect score on 150 samples the author wrote is a statement about the corpus, not about the
detector. The Wilson interval says so out loud — 0 false positives in 75 benign samples means the
true rate lies somewhere in **[0%, 4.9%]**, not at zero. The corpus cannot resolve anything finer
than about one in twenty.

Two consequences that the report must carry rather than bury:

- **Stage 2's value is currently unmeasurable here.** It changes no metric, because there is no
  headroom left in which to change one. What it buys is *capability* — `PERSON`, `ORGANIZATION`,
  `LOCATION`, categories Stage 1 structurally cannot reach — at a 200× latency cost that is still
  invisible to a user. Task 4.3 extends the corpus so that capability becomes measurable.
- **The regex-only baseline is now also at 100% precision**, so "Stage 2 reduces false positives"
  is *not* demonstrated by this run and is not claimed.

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
| Dashboard: React vs. Flask + Jinja | Week 6 |
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
│   │   ├── ui/                 the warning modal, in a Shadow DOM so ChatGPT's CSS cannot
│   │   │                       break our styling and vice versa.  [empty]
│   │   └── shared/             message-type constants used by both worlds — prevents
│   │                           typo'd message names failing silently.
│   └── assets/icons/           toolbar icons (16/48/128px).  [empty — Week 8 polish]
│
├── backend/                    ← Stages 1-5. All the intelligence.  [empty]
│   ├── app/
│   │   ├── main.py             FastAPI app creation, CORS, router mounting. Entry point.
│   │   ├── api/routes/         HTTP endpoints ONLY — parse request, call pipeline, return.
│   │   │                       No detection logic here.
│   │   ├── schemas/            Pydantic models = the typed contract between extension and
│   │   │                       backend. Free validation + interactive API docs.
│   │   ├── detectors/          one module per technique: regex.py, entropy.py, presidio.py,
│   │   │                       ocr.py, behavioral.py. Each pure and independently testable.
│   │   ├── pipeline/           the orchestrator: runs stages, short-circuits on critical
│   │   │                       findings, merges results.
│   │   ├── core/               risk_engine.py (weighted fusion), config.py (ALL tunable
│   │   │                       weights and thresholds in one place).
│   │   ├── models/             SQLAlchemy tables (Event, UserProfile).
│   │   └── db/                 session management and initialisation.
│   └── tests/                  pytest suite, written alongside each detector.
│
├── evaluation/                 ← your grade depends on this folder  [empty]
│   ├── data/                   labelled corpus: text samples + ground-truth labels.
│   ├── results/                generated metrics (gitignored — regenerable).
│   └── run_eval.py             loads corpus, runs pipeline, prints precision/recall/FPR.
│
├── dashboard/                  ← admin UI  [empty — tech choice open]
├── docs/diagrams/              ← architecture diagrams, report material, demo script  [empty]
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
| 2.6 Paste and image-upload interception | Console shows pasted text and file bytes | ⏭️ **deferred to Week 6** — see note |

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
| 4.3 Extend the corpus with person/organisation PII | Stage 2's contribution becomes measurable | ⬜ **next** |

> **The plan changed, and the reason is worth recording.** Week 4 was written to end with "the
> false-positive drop vs. Week 3 — the single most important number in the report." That number
> does not exist, because 4.2 took the false-positive rate to **zero on regex alone**. Stage 2 has
> no false positives left to remove.
>
> This is a corpus limitation, not a Stage 2 failure, and 4.3 is the honest response: extend the
> corpus to cover the PII categories only Stage 2 can reach, and report the pre- and
> post-extension numbers side by side. See **[D30](docs/decisions.md)** for why the extension is
> specified in advance rather than tuned to the outcome.

### Week 5 — Stage 5 + persistence
Risk engine (weighted fusion, tunable config file) · thresholds → allow/warn/redact/block ·
redaction implementation · SQLAlchemy models · SQLite schema · event logging · latency measurement.

### Week 6 — Stage 3 (OCR) + Gemini adapter
Tesseract · image preprocessing (where OCR accuracy is won or lost) · feeding OCR text back
through Stages 1–2 · then the Gemini site adapter.

### Week 7 — Stage 4 + dashboard
Rolling behavioral baseline (z-scores over a user's own history) · dashboard UI · charts.

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
confusion.

### Backend (not built yet — Week 3)

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

### Toolchain status

| Tool | State | Notes |
|---|---|---|
| Node 22.18 / npm 10.9 | ✅ | For the dashboard later |
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
1941d40  chore: ignore .claude/ local tool-permission state
dc124df  docs: add README as living session-recovery document
06d0ca3  feat(extension): add service worker and content<->worker message bridge
17a6640  feat(extension): inject content script into ChatGPT
9214afa  feat(extension): add minimal Manifest V3 declaration
f882ec8  chore: initialise ShadowGuard AI project skeleton
```
