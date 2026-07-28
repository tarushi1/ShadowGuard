# ShadowGuard AI

**A Context-Aware, Multimodal Data Loss Prevention Framework for Enterprise Interactions with Generative AI Platforms.**

ShadowGuard sits between an employee and a browser-based generative-AI platform (ChatGPT, Gemini)
and inspects everything — typed text, pasted text, uploaded images — *before* it is sent.
It computes a risk score and decides whether to **allow**, **warn**, **redact**, or **block**.

> **This README is the session-recovery document.** If a Claude session expires or context is lost,
> read this file first: it records the current state, every locked decision, and the exact next task.
> It is kept up to date as work progresses.

---

## Status at a glance

| | |
|---|---|
| **Last updated** | 2026-07-28 |
| **Project root** | `/Users/arushitiwari/Desktop/ShadowGuard` |
| **Git branch** | `main` (4 commits, working tree clean) |
| **Current stage** | Stage 0 — Chrome extension scaffolding |
| **Completed** | Tasks 1.1 – 1.4 |
| **Next task** | **1.5 — locate ChatGPT's composer element reliably** |
| **Deadline** | ~2026-09-20 (8 weeks from 2026-07-26) |
| **Weeks elapsed** | ~0.3 of 8 |

### What actually runs today

Loading `extension/` unpacked into Chrome and opening `chatgpt.com` produces, in the page console:

```
 [ShadowGuard] ACTIVE            ← green badge
[ShadowGuard] version    : 0.1.0
[ShadowGuard] host       : chatgpt.com
[ShadowGuard] readyState : complete
[ShadowGuard] frame      : top-level
[ShadowGuard] candidate input elements: 1
[ShadowGuard] bridge OK — reply from service worker: {type: "SG_PONG", ...}
```

Nothing is intercepted yet, and there is no backend yet. The extension proves only that our code
runs inside the page and can talk to the service worker.

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
│   │   ├── adapters/           one file per platform (chatgpt.js, gemini.js). Each knows
│   │   │                       only "where is the composer / send button / how do I send".
│   │   │                       This is why adding Gemini is cheap later.  [empty]
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
| `extension/manifest.json` | 30 | MV3 declaration: name, version, service worker registration, content-script injection rules for `chatgpt.com` and `chat.openai.com` |
| `extension/src/shared/messages.js` | 41 | Frozen `SG.MSG` constants — the shared message vocabulary. Loaded by **both** worlds (manifest for content, `importScripts` for worker) |
| `extension/src/content/content.js` | 103 | World 2. Re-injection guard, activation banner, DOM-access proof, and a PING across the bridge |
| `extension/src/background/service_worker.js` | 89 | World 3. Lifecycle logging and the message router; replies PONG to PING |

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
| 1.5 Locate ChatGPT's composer element reliably | You can read the typed text from the console | ⬜ **next** |

### Week 2 — Stage 0b: the interception itself (hardest week)

| Task | Test | Status |
|---|---|---|
| 2.1 Capture-phase `keydown` listener cancels Enter | Press Enter, nothing sends, console logs the text | ⬜ |
| 2.2 Intercept the send-button click path | Clicking the arrow is also blocked | ⬜ |
| 2.3 Replay the send programmatically after approval | Message sends after a fake "approve" | ⬜ |
| 2.4 Inject the modal UI (Shadow DOM) | Modal renders correctly over the page | ⬜ |
| 2.5 Wire modal → Cancel discards, Proceed sends | Full loop works with a hardcoded verdict | ⬜ |
| 2.6 Paste and image-upload interception | Console shows pasted text and file bytes | ⬜ |

### Week 3 — Stage 1 + evaluation foundation

| Task | Test | Status |
|---|---|---|
| 3.1 Python 3.12 + venv + `fastapi uvicorn` | `/docs` opens in the browser | ⬜ |
| 3.2 `POST /api/v1/analyze` returning a stub verdict | `curl` gets JSON back | ⬜ |
| 3.3 Connect extension → backend end-to-end | Real modal driven by a real HTTP response ← **first true milestone** | ⬜ |
| 3.4 Regex detector module (API keys, cards, emails, IBAN, JWT) | Unit tests pass on positive + negative cases | ⬜ |
| 3.5 Shannon entropy scorer | High on random keys, low on English prose | ⬜ |
| 3.6 Labelled test corpus (~150 samples) | A CSV you can rerun forever | ⬜ |
| 3.7 Evaluation harness (precision / recall / FPR) | A printed metrics table | ⬜ |

### Week 4 — Stage 2: semantic analysis
Presidio setup · spaCy model · custom recognizers · context words · confidence scores · merging
Stage 1 + 2 results without double-counting · re-run the harness and show the false-positive drop
vs. Week 3. **That delta is the single most important number in the report.**

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

---

## Git history

```
06d0ca3  feat(extension): add service worker and content<->worker message bridge
17a6640  feat(extension): inject content script into ChatGPT
9214afa  feat(extension): add minimal Manifest V3 declaration
f882ec8  chore: initialise ShadowGuard AI project skeleton
```
