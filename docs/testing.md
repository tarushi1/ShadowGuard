# Testing guide

How to verify every component, what "working" looks like, and how to debug when it is not.

---

## Running everything

**Terminal — backend** (leave open):
```bash
cd ~/Desktop/ShadowGuard
source .venv/bin/activate
uvicorn backend.app.main:app --reload
```

**Terminal — tests:**
```bash
cd ~/Desktop/ShadowGuard && source .venv/bin/activate
pytest -q
```

**Chrome — extension:**
1. `chrome://extensions` → **Developer mode** ON
2. **Load unpacked** → `~/Desktop/ShadowGuard/extension`
3. After any code change: click **↻** on the card, **then** reload the ChatGPT tab

> Editing the **manifest or service worker** needs the extension reloaded.
> Editing a **content script** needs the page reloaded.
> Skipping either is the most common "my change did nothing" confusion.

---

## The console context trap

`__sg` lives in the content script's **isolated world**. The DevTools console defaults to the
page's world, where it does not exist — and **it resets to `top` on every page reload**.

> Console panel → context dropdown (top-left, reads `top`) → **ShadowGuard AI**

Confirm before doing anything else:
```js
typeof window.__shadowguardActive    // "boolean" = right context, "undefined" = still on `top`
```

This single thing costs beginners hours. It is not a bug — it is world isolation working.

Also: set the log-level filter to **Verbose**, or `console.debug` lines (the "why did this pass
through" messages) stay hidden.

---

## Debug handles

```js
__sg.read()          // current composer text
__sg.isEmpty()       // ProseMirror-aware empty check
__sg.composer        // the live element
__sg.watcher.find()  // {element, selector, tier}
__sg.sendButton()    // where a replay would route right now
__sg.pending         // the held send, or null
__sg.approve()       // manual "Proceed"
__sg.discard()       // manual "Cancel"
__sg.disable()       // stop intercepting, so the tab stays usable
__sg.enable()        // resume
__sg.stats()         // {seen, intercepted, passed} — proves guards fire
__sg.modal           // the modal instance (its shadow root is closed; this is the only handle)
```

**Remember `__sg.disable()` exists.** With every send path closed, that tab cannot send anything
until you re-enable or a verdict allows it.

---

## Extension test matrix

Backend must be running for rows 1–5.

| # | Do | Expect |
|---|---|---|
| 1 | Type `how do I reverse a string in python`, Enter | **Sends normally, no modal.** Server logs `action=allow`. |
| 2 | Type `the db password is hunter2`, Enter | 🔴 modal, score 70. Cancel → nothing sends, text stays. |
| 3 | Same, then **Send anyway** | Sends |
| 4 | Type `my key is sk-proj-abc123`, Enter | 🟣 critical, **no override button** |
| 5 | Same via the **arrow button** | Same modal, `trigger: send-button` |
| 6 | **Stop the server**, send anything | ⬜ **grey** "offline, not inspected" modal, sending focused |
| 7 | Restart server, send again | Normal verdicts resume |
| 8 | **Shift+Enter** | Newline. Verbose log: *"Shift+Enter (newline)"* |
| 9 | Empty box, Enter | Passes through — *"composer is empty"* |
| 10 | ⌘K search box, Enter | Passes through — *"target is outside the composer"* |
| 11 | Click sidebar / model picker / anything | **Nothing logged, nothing breaks** |
| 12 | Enter **while a modal is open** | *"a decision is already pending"*. No crash, no second dialog. |
| 13 | Send, then `__sg.sendButton()` **while the reply streams** | `null` |
| 14 | Switch conversation in the sidebar | `composer RE-attached`. Interception still works. |
| 15 | Esc on a warning modal | Treated as Cancel |

### The rows that matter most

- **1** — ordinary messages must flow with the user never knowing ShadowGuard exists. If every
  message showed a dialog the tool would be uninstalled in a day. The adoption failure mode is as
  real as the detection one.
- **11** — a `click` listener on `window` sees every click on the page. A delegation bug here
  breaks ChatGPT in ways that look like ChatGPT's fault.
- **12** — without the busy guard this is a *security* bug, not a crash: the second message would
  be sent under an approval given for the first.
- **13** — ChatGPT swaps send for a **stop** button while streaming. A replay that clicks it
  aborts the assistant's reply instead of sending. The failure that embarrasses you live.
- **14** — proves the `MutationObserver` earns its keep. A one-shot `querySelector` fails here.

---

## Backend testing

### Interactive — Swagger UI

**http://127.0.0.1:8000/docs** → expand `POST /api/v1/analyze` → **Try it out** → paste into the
Request body → **Execute**.

```json
{"text": "how do I sort a list in python", "user_id": "test", "platform": "chatgpt"}
```
→ `allow`, score 0

```json
{"text": "the database password is hunter2", "user_id": "test", "platform": "chatgpt"}
```
→ `warn`, score 70

```json
{"text": "my key is sk-proj-abc123def456", "user_id": "test", "platform": "chatgpt"}
```
→ `block`, score 95

```json
{"text": "hello", "platform": "chatgpt"}
```
→ **422**, `"loc": ["body","user_id"]`. Nobody wrote that message; Pydantic generated it.

### Command line

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text":"my key is sk-proj-abc123","user_id":"test","platform":"chatgpt"}'
```

### Automated

```bash
pytest -q                                    # expect: 73 passed
pytest backend/tests/test_regex_detector.py -v   # detector only
pytest -v                                    # per-test names
```

The detector suite is structured around the two ways detection fails:

- **Positive cases → recall.** What we miss is a *security* failure.
- **Negative cases → precision.** What we wrongly flag is an *adoption* failure, and a tool
  people disable has a recall of zero.

The negative cases carry equal weight. It is trivial to write a detector that catches everything
by flagging everything; the negative tests are what stop the module drifting that way.

The suite asserts the **contract**, not the current detector's opinions — so it keeps working
when the stub is replaced by real detectors.

---

## Correlating a request across systems

Every request carries a `request_id`. The same string appears in:

1. the browser console — `id 1ebfbf73d282`
2. the server terminal — `analyze id=1ebfbf73d282 ... action=block`
3. from Week 5, the SQLite event row

That is what turns three separate systems into one traceable story when someone says *"it blocked
something it shouldn't have."*

---

## Debugging by symptom

| Symptom | Cause |
|---|---|
| `__sg is not defined` | Console context is `top`. Resets on every reload. |
| Changes had no effect | Reload the extension **and** the page |
| Always the offline modal | Backend not running, or wrong port |
| `Failed to fetch` in worker console | `host_permissions` missing — needs an **extension** reload |
| Message still sends | Bubble phase instead of capture, or listener on the composer instead of `window` |
| Send blocked but a **blank line** appears | Missing `preventDefault()` |
| Whole page unclickable | Click handler cancelling unconditionally instead of only on a send-button match |
| Tier warning (`tier 4/6`) | ChatGPT markup moved. Inspect the composer, add the new stable attribute as tier 1. **This is the intended workflow, not a failure.** |
| Replay aborts a streaming reply | Stop-button exclusion missing from `findSendButton()` |
| Infinite loop of blocked sends | `isTrusted` guard removed — the replay re-triggers interception |
| `ModuleNotFoundError: backend` | Running pytest/uvicorn from inside `backend/`. Run from the project root. |
| `pytest: command not found` | venv not activated — check the prompt for `(.venv)` |
| Nothing in server terminal | Requests are not arriving. Check the worker console for fetch errors. |

### Where each component logs

| Component | Where |
|---|---|
| Content script | ChatGPT page DevTools console (context: **ShadowGuard AI**) |
| Service worker | `chrome://extensions` → **service worker** link (a separate window) |
| Extension errors | `chrome://extensions` → **Errors** button — includes warnings, so red does not always mean broken |
| Backend | the terminal running uvicorn |
