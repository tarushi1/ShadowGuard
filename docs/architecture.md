# Architecture

## The organising principle

> **The browser detects and enforces. The server decides.**

The extension is deliberately unintelligent. It knows how to cancel a send, how to ask a
question, and how to replay a send. It contains **no detection logic whatsoever** — not one
regex, not one keyword list.

Three reasons, in order of importance:

1. **Measurability.** The evaluation harness is Python and calls the detectors directly, in
   process, over a labelled corpus. Anything implemented in JavaScript is invisible to it and
   therefore contributes nothing to the precision/recall numbers. *Logic the harness cannot call
   does not exist as far as the report is concerned.*
2. **Deployability.** Detection rules change constantly — a new API-key format ships every month.
   Updating a server is instant; updating an extension means pushing to 5,000 employees and
   waiting. This is how commercial DLP (Netskope, Zscaler, Nightfall) is built.
3. **Single implementation.** Logic split across two languages is logic that drifts.

## Component map

```
┌─────────────────── CHROME (tab open on chatgpt.com) ────────────────────────┐
│                                                                             │
│   ChatGPT's own React page  ── not our code, we are a guest here            │
│         ▲  user types / pastes / uploads image                              │
│         │                                                                   │
│   ┌─────┴───────────────┐   injects   ┌──────────────────────────┐          │
│   │   CONTENT SCRIPT    │────────────►│  WARNING MODAL (our UI)  │          │
│   │  (runs in the page) │◄────────────│  [ Cancel ] [ Proceed ]  │          │
│   └─────┬───────────────┘   decision  └──────────────────────────┘          │
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
          │  { text, image_b64?, user_id, platform, trigger, client_version }
          ▼
┌─────────────────── FASTAPI BACKEND (127.0.0.1:8000) ────────────────────────┐
│                                                                             │
│   route handler ──► PIPELINE ORCHESTRATOR                                   │
│                          │                                                  │
│    ┌─────────────────────┼──────────────┬──────────────┐                    │
│    ▼                     ▼              ▼              ▼                    │
│  STAGE 1              STAGE 2        STAGE 3       STAGE 4                  │
│  regex + entropy      Presidio       Tesseract     behavioural              │
│  ~1ms, catches        semantic NER   OCR on        rolling baseline         │
│  API keys, cards      names, orgs    images        per user                 │
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
│              action: allow / warn /               ┌────────────────┐        │
│              redact / block  ──────────────►      │     SQLITE     │        │
│                        │                          │ events         │        │
│                        │                          │ user_profiles  │        │
│                        │                          └───────┬────────┘        │
└────────────────────────┼──────────────────────────────────┼─────────────────┘
                         │ JSON verdict back to extension   │ read-only
                         ▼                                  ▼
                (modal shown to user)            ┌─────────────────┐
                                                 │ ADMIN DASHBOARD │
                                                 └─────────────────┘
```

## The three JavaScript worlds

Extension code does not run in one place. It runs in three contexts that **cannot see each
other's variables**. This is Chrome's security model, and it dictates the entire design.

| World | What it is | Can | Cannot |
|---|---|---|---|
| **1 — The page** | ChatGPT's own React code | — | *(not our code)* |
| **2 — Content script** | Our JS, injected into the tab | Read/modify the DOM, attach listeners, render the modal | See the page's JS variables; make cross-origin HTTP calls |
| **3 — Service worker** | Our JS, one instance for the whole extension | Full `chrome.*` API, cross-origin `fetch()` | Touch the DOM. Also **dies after ~30s idle** |

Worlds 2 and 3 communicate **only** by passing JSON messages.

Two consequences that produce real bugs:

1. The content script must intercept the Enter key (it needs the DOM), but only the service
   worker can call FastAPI (it needs the network). **Neither can do the job alone.** The split
   in our codebase is not a style choice — Chrome made it for us.
2. **Never store state in a service-worker global.** Chrome kills the worker after ~30 seconds
   idle and every global is lost. The user id lives in `chrome.storage` for exactly this reason;
   a generated id in a `let` would silently become a *new* id on every wake-up, and the
   behavioural stage would see an endless stream of first-time users.

## Request trace — pasting an API key

1. Content script's **capture-phase** `keydown` listener on `window` fires *before* ChatGPT's own
   handler. `stopImmediatePropagation()` + `preventDefault()` — the message does not send.
2. Content script reads the composer text and sends `SG_ANALYZE_REQUEST` to the service worker.
3. Worker stamps the persistent `user_id` and `fetch()`es `POST /api/v1/analyze`, bounded by a
   3-second `AbortController` timeout.
4. Backend: Stage 1 regex matches the key pattern → entropy confirms high randomness → CRITICAL.
   Because Stage 1 already found a critical secret, the orchestrator short-circuits and skips
   Stage 2 (~50ms of spaCy saved). `stages_run` records what actually ran.
5. Stage 4 raises risk if this is the 5th such attempt today. Stage 5 fuses → score 95 → `block`.
6. Event row written to SQLite. JSON returned — **offsets, never the matched text**.
7. Worker relays to content script → modal renders in a closed Shadow DOM → user clicks Cancel;
   or on `warn`, Proceed dispatches a synthetic (untrusted) send that the interceptor's
   `isTrusted` guard lets straight through.

## The central constraint

**JavaScript cannot pause an event.** There is no `event.hold()`, and a listener is synchronous —
the moment it returns, the browser moves on. Our backend call is asynchronous. So
"inspect before sending" is not literally possible. What is possible:

```
cancel  →  stash the text  →  analyse  →  replay on approval
```

Every design decision in Stage 0 follows from that single fact.

## Layering rules

| Layer | Owns | Must not |
|---|---|---|
| `extension/src/adapters/` | *All* platform-specific DOM knowledge, one file per platform | Contain detection logic or orchestration |
| `extension/src/content/` | Interception, composer tracking, orchestration | Contain site-specific selectors |
| `extension/src/background/` | Every network call, persistent state | Touch the DOM |
| `backend/app/api/routes/` | HTTP: parse → call pipeline → log → respond | Contain detection logic |
| `backend/app/pipeline/` | Stage sequencing, short-circuiting, result merging | Know about HTTP |
| `backend/app/detectors/` | Pure detection functions | Perform I/O or know about HTTP |
| `backend/app/core/` | Risk fusion, all tunable weights and thresholds | Be scattered across other modules |
| `backend/app/models/` | The database schema | Contain a column that can hold prompt text |
| `backend/app/db/` | The **only** module that writes to the database | Accept the user's text as an argument |

The adapter layer is why adding Gemini in Week 6 is **one new file**, not a rewrite: it cannot
break ChatGPT because it does not touch any ChatGPT code path.

The detectors/routes split is why the evaluation harness can run hundreds of samples in
milliseconds with no server process.

The `db`/`models` split carries a privacy rule rather than a tidiness one. `record_decision()`
takes the **response** — already stripped of user content by the API schema — and a `char_count`
computed by its caller, so it never receives the text at all. Storing a prompt would require
adding a column, changing that signature, and deleting a test that searches the database file for
secrets. That is a deliberate act, which is the point: *"do not store prompt text"* is one
convenient five-line change away from being false if it is only a rule people remember.
