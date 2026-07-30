# Concepts

Every technical idea this project uses, explained from zero. Grouped by area; each entry says
what it is, why it matters *here*, and where it is used.

---

## Browser extensions

### Manifest V3
The current Chrome extension format; V2 stopped running in 2024, so this is not a choice.
The change that affects us: V2 had a **persistent background page** that could hold state in
memory indefinitely. V3 replaced it with an **ephemeral service worker** that Chrome terminates
after ~30 seconds of inactivity, for memory and battery reasons.

*Consequence:* any state that must survive goes in `chrome.storage`, never a global variable.
This causes a bug that looks like magic if you do not know it.
→ `extension/manifest.json`, `extension/src/background/service_worker.js`

### Content script
JavaScript the browser injects into a matching page. Shares the **DOM** with the page but runs
in an **isolated world** — a separate JavaScript heap. It cannot read the page's variables, and
the page cannot read ours. It also cannot make cross-origin HTTP requests.

*Why the isolation exists:* if content scripts could make arbitrary cross-origin requests, every
extension on every page would be a data-exfiltration tool.
→ `extension/src/content/`

### Service worker (background)
One instance shared by all tabs. No DOM at all. Full `chrome.*` API access, and — with
`host_permissions` — permitted to make cross-origin requests. This is Chrome deliberately
funnelling extension network access through a single auditable choke point.
→ `extension/src/background/service_worker.js`

### `host_permissions`
Declares which hosts the extension may contact. Produces an install-time warning to the user
("Read and change your data on 127.0.0.1"), which is the visible cost of every permission.
We started with **zero** permissions and add each one only when a task needs it — the principle
of least privilege, verifiable in the git history.

### `chrome.storage`
Persistent key-value storage that survives service-worker termination and browser restarts.
Used for the installation's `user_id`.

---

## DOM and events

### Event phases: capture, target, bubble
An event does not just fire on the element you interacted with. It travels a path:

```
   CAPTURE (downward)          window → document → … → parent
   TARGET                      the element itself
   BUBBLE  (upward)            parent → … → document → window
```

`addEventListener(type, fn)` defaults to **bubble**. For interception that is too late — the
platform's handler has already run and the message is gone. `{capture: true}` on an **ancestor**
is the only placement the DOM spec guarantees runs first.

**Subtle trap:** attaching capture to the *target itself* is not enough. At the target element,
capture and bubble listeners fire in **registration order**, and ChatGPT registered first.
That is why our listeners are on `window`.
→ `extension/src/content/interceptor.js`

### `preventDefault()` vs `stopPropagation()` vs `stopImmediatePropagation()`
Not interchangeable, and the difference is the whole task:

| Method | Effect |
|---|---|
| `preventDefault()` | Cancels the **browser's built-in action** — inserting a newline, submitting a form, following a link. Every other listener still runs. |
| `stopPropagation()` | Stops the event travelling further along the path. Listeners on the **same** element still run. |
| `stopImmediatePropagation()` | Stops propagation **and** blocks remaining listeners on the same element. |

ChatGPT sending a message is **their JavaScript handler**, not a browser default action — so
`preventDefault()` alone does nothing to stop it. Both calls are required, and omitting either
produces a distinct visible bug.

### `event.isTrusted`
`true` only for events the browser generated from real user input; anything dispatched from
JavaScript is `false`, and it cannot be forged. We use it as the replay mechanism: our
programmatic send passes through the interceptor automatically, so there is no
"am I replaying?" flag to set, clear, and eventually leave stuck after an exception.

### Event delegation and `Element.closest()`
Instead of N listeners on N elements that come and go, one listener on a stable ancestor that
identifies the origin per event.

`closest(selector)` walks **up** from an element through its ancestors and returns the first
match. Essential because the send arrow is a `<button>` wrapping an `<svg>` wrapping a `<path>` —
the event target is the `<path>`, so `event.target.matches(buttonSelector)` is false essentially
always and **fails silently**.

Delegation also survives the platform remounting the button (it swaps to a *stop* button while a
reply streams), which a stored reference would not.

### `MutationObserver`
A browser API that calls your function whenever the DOM changes beneath a node. An inversion of
control: instead of polling "is it there yet?" 120 times a minute, the browser tells you once, at
the moment it appears. Zero cost while idle.

We use `{childList: true, subtree: true}` — nodes added or removed anywhere below the root. We
deliberately do *not* set `attributes` or `characterData`, which would fire on every keystroke.
→ `extension/src/content/composer_watcher.js`

### `element.isConnected`
A browser-maintained boolean: is this node still attached to the document? Far cheaper and more
accurate than walking parents. The precise question to ask when checking whether a stored element
reference has gone stale.

### SPA navigation
In a Single Page Application, clicking a link changes the URL without reloading the page. React
tears down the old DOM and builds new nodes. **Content scripts do not re-run.** A listener
attached to a node that has since been replaced still "works" — it just never fires again, with
no error anywhere. This is why the composer handle is watched rather than fetched once.

### Shadow DOM
`element.attachShadow()` creates a DOM subtree with its own style scope. Page CSS does not reach
in; our CSS does not leak out.

*Why it matters here:* a DLP warning rendered invisible by the host page's CSS is a **failure to
warn**, not a cosmetic bug. One leak to know: **inherited** properties (`font-family`, `color`,
`line-height`) still cross the boundary — `:host { all: initial; }` closes that.

**`open` vs `closed`:** with `open`, any page script can reach `host.shadowRoot`, query our
Proceed button and click it. We use **`closed`** so only our closure holds the reference.
*Honest limit:* `closed` stops the page reaching *into* our UI; it cannot stop the page finding
and deleting the host element.
→ `extension/src/ui/modal.js`

### `<dialog>` and the top layer
`dialog.showModal()` promotes the element to the browser's **top layer**, which renders above the
entire page regardless of `z-index`. This matters because **stacking contexts** — any ancestor
with `transform`, `filter`, `opacity < 1` or `will-change` — trap descendants' z-index, so
`z-index: 999999` inside one can still paint below a sibling with `z-index: 1`.

It also provides focus trapping, `Esc` handling, `::backdrop` and `aria-modal` semantics for
free. Hand-rolling correct focus trapping is hard and frequently botched.

### React controlled components
In React, an input's displayed value is a projection of React's internal state, not the source of
truth. Setting `element.value = "text"` updates the DOM briefly, React's state never learns, and
the next render overwrites it. Working around this needs the **native value setter** React
shadowed, plus a synthetic `input` event:

```js
const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
setter.call(el, text);
el.dispatchEvent(new Event("input", { bubbles: true }));
```

**Not needed for replay** — we cancelled the send *event*, never the text, so it is still in the
box and React's state already holds it. It *will* be needed in Week 5 for the `redact` action,
the only case that rewrites the user's text.

### IME composition (`isComposing`, `keyCode === 229`)
For Japanese, Chinese and Korean input, the user types phonetic characters, an Input Method
Editor offers candidates, and **Enter confirms the selected candidate** — it does not send.
Swallowing it would make the composer unusable for a large share of the world's users. An
internationalisation *correctness* issue, not a nicety.

### `innerText` vs `textContent`
`textContent` concatenates raw text nodes and ignores layout. `innerText` respects block
boundaries. ProseMirror renders one `<p>` per visual line, so `textContent` turns
`<p>sk-proj-abc</p><p>def123</p>` into `"sk-proj-abcdef123"` — two lines fused into something
that looks exactly like one API key. A **detection-accuracy** decision, not formatting.

### Debouncing
Waiting until activity stops before acting. Without it, a handler on `input` fires per keystroke;
with a 400ms debounce it fires once after typing stops. Matters more once each call is an HTTP
request.

### `AbortController`
`fetch()` has **no default timeout** and will wait indefinitely. `AbortController` is the only
way to bound it. Without one, a hung backend means a send that never resolves and a composer the
user can never clear.

### `fetch()` and `response.ok`
`fetch()` only rejects on **network** failure. A 500, 422 or 404 all resolve *successfully* with
`ok === false`. Forgetting the explicit check is how an error page gets parsed as a verdict.

---

## Python and the backend

### Virtual environment (venv)
A folder with its own Python interpreter and its own `site-packages`. `pip install` writes there
instead of into the system Python.

Two reasons: **isolation** (Presidio pins spaCy versions, spaCy pins numpy versions — installed
globally they collide with other projects) and **reproducibility** (`pip freeze > requirements.txt`
lets an evaluator recreate your environment exactly; *"it works on my machine"* is not a
defensible answer).

`.venv/` is gitignored — it is hundreds of megabytes of platform-specific binaries. You commit
the *recipe*, not the result.

**The most common confusion:** a new terminal is not activated. If `pip install` seems to do
nothing, check the prompt for `(.venv)`.

### ASGI, FastAPI, Uvicorn
**ASGI** is the Python async web-server interface. **FastAPI** defines *what* endpoints do;
**Uvicorn** opens the socket and speaks HTTP. They are separate so either can be swapped.

FastAPI reads Python **type hints** and generates request validation, error responses and
interactive documentation from them. You declare the data shape once and get three things.

| | FastAPI | Flask | Django |
|---|---|---|---|
| Validation | automatic from type hints | manual | forms/serializers |
| Interactive docs | built in (`/docs`) | third-party | third-party |
| Async | native | bolted on | partial |

### Pydantic
The library doing that validation. A `BaseModel` subclass **is** the API specification: runtime
validation, the OpenAPI document, and the `/docs` form all derive from one declaration, so there
is no separate spec to drift out of sync. Rust-backed, fast enough for a latency-sensitive path.
→ `backend/app/schemas/analyze.py`

### `def` vs `async def` in FastAPI
FastAPI runs plain `def` handlers in a **thread pool**. An `async def` that never awaits blocks
the event loop for its entire duration and stalls every other request. Our detection stages are
CPU-bound (regex, entropy, spaCy inference), so synchronous is correct — and honest.

### CORS
Browsers refuse cross-origin requests unless the server opts in. **The service worker does not
need this**: it runs with the extension's origin and Chrome grants it `host_permissions` hosts.
Our CORS middleware exists for the Week 7 dashboard, which *is* an ordinary web page.
Knowing which component needs it and which does not is the point.

### `APIRouter` and API versioning
A router groups endpoints under a shared prefix, declared once. `/api/v1/` exists because the
extension and backend deploy **independently** — someone will run an old extension against a new
backend. Breaking changes go to `/api/v2/` while `/api/v1/` keeps old clients alive.

### pytest
`TestClient` drives the real app in-process — routing, validation and serialisation all execute
as they would over the network, with no server, no port, no network stack. 14 tests run in 0.13s,
which is what makes it realistic to run them on every change.

---

## Detection and evaluation

### Luhn algorithm (mod-10 checksum)
Double every second digit from the right; if doubling exceeds 9, subtract 9. The total must be
divisible by 10. Essentially every payment card satisfies it.

*Why it matters here:* `\d{16}` matches every order number, invoice id and tracking code in
existence. Roughly nine in ten random 16-digit strings **fail** Luhn, so one validator function
cuts that rule's false positives by about an order of magnitude. It is a checksum, not a
signature — it catches typos and random strings, not deliberate forgery, which is exactly what is
needed. **This is the project's thesis in miniature: validation beats pattern matching.**
→ `backend/app/detectors/regex_detector.py`

### ReDoS (catastrophic backtracking)
Nested quantifiers such as `(a+)+` can make a regex take **exponential** time, so a 30-character
input hangs a server for minutes. A real denial-of-service class, not a theoretical one.

*Mitigations here:* every pattern uses **bounded** quantifiers and avoids nesting; the API caps
input at 100,000 characters; and a test asserts a 100 KB scan completes in under two seconds, so
it fails loudly if someone adds an unsafe pattern.

### Confidence vs severity
Two different questions, deliberately separate fields on a `Finding`:

- **Confidence** — how sure is the detector that it found what it claims? A
  `-----BEGIN PRIVATE KEY-----` header is 1.0; a bare 12-digit number is 0.7.
- **Severity** — how bad would this be *if true*? A property of the entity **type**.

Conflating them causes real bugs: ranking overlapping matches by severity first let a weak
`critical` guess beat a checksum-validated `high` match. See [D16](decisions.md#d16--overlap-resolution-ranks-confidence-before-severity).

### Shannon entropy
Information density in **bits per character**: `H = -Σ p(c) log₂ p(c)`. Intuitively, how surprised
you are on average by the next character. `"aaaa"` is 0 — you always know what comes next.

Catches secrets whose **format is unknown** — the complement to regex, which only catches formats
you anticipated. Neither subsumes the other: regex has near-perfect precision and zero recall on
unknown formats; entropy has broad recall and mediocre precision.

**The naive version does not work**, for two measured reasons:

1. **Entropy is capped by length.** A string of length *n* carries at most `log₂(n)` bits per
   character. A 20-character token maxes out at 4.32 bits *however random it is*, so the commonly
   quoted 4.5 threshold silently never fires on short tokens.
2. **English is high-entropy at this scale.** Letters rarely repeat inside twenty characters.
   Measured on our own samples:

   | string | H |
   |---|---|
   | `thequickbrownfoxjump` (English) | **4.12** |
   | `a3f5c9e17b2d48f6a0c3e5b7d9f1a2c4` (random hex) | **3.93** |

   English scores *higher* than a random hex key. A raw threshold flags prose and misses the secret.

**What actually discriminates: character-class diversity.** Random base64 uses all three of
{lowercase, uppercase, digit}; hex uses two; English prose and `snake_case` identifiers use one.
That single check removes the entire prose false-positive class.

**Charset-aware thresholds.** Hex is capped at 4.0 bits by its 16-symbol alphabet, base64 reaches
6.0. One threshold for both would either miss every hex secret or flood on base64. Ours:
hex ≥ 3.5, general ≥ 4.2.

**It stays a weak signal**, deliberately. Base64 images, minified JS, git SHAs and UUIDs are all
high-entropy and harmless. Findings cap at `medium` severity and 0.85 confidence: random-looking
is *suspicious*, not proven. This stage warns and never blocks alone — which is exactly why the
Week 5 risk engine exists, to weigh signals rather than act on any one.
→ `backend/app/detectors/entropy_detector.py`

### Precision, recall, false-positive rate
The numbers the report is graded on:

| Metric | Formula | Question it answers |
|---|---|---|
| **Precision** | TP / (TP + FP) | When we flag something, how often are we right? |
| **Recall** | TP / (TP + FN) | Of everything sensitive, how much did we catch? |
| **FPR** | FP / (FP + TN) | How often do we cry wolf on benign text? |

The asymmetry matters and belongs in the report: **false negatives are the security failure;
false positives are the adoption failure.** A tool that cries wolf gets disabled, at which point
its recall is zero.

### Base rate and why balanced-corpus precision lies
Our corpus is 50/50 sensitive/benign, which gives reliable **recall** and **FPR** — both have 75
samples behind them. But **precision measured on a balanced corpus overstates real precision**,
because real traffic is perhaps 1% sensitive.

With recall *r*, false-positive rate *f*, and prevalence *p*:

```
precision_real = (r · p) / (r · p + f · (1 − p))
```

At *p* = 0.01, a system with 90% recall and a 5% FPR reports ~95% precision on a balanced corpus
but achieves **~15%** in reality. This is the base-rate fallacy, and it is the single most common
way security-tool evaluations mislead. The harness prints both figures.
→ `evaluation/README.md`

### Short-circuiting
When Stage 1 finds a critical secret, the pipeline skips Stage 2 — no point spending ~50ms of
spaCy to confirm something already certain. `stages_run` in the response records what actually
executed, making this **visible evidence** rather than a claim.

### Named Entity Recognition (NER)
A statistical model that labels spans by what they *refer to* — `PERSON`, `ORGANIZATION`,
`LOCATION` — using **surrounding words** rather than the shape of the token:

```
"Sarah Chen approved the refund"      → Sarah Chen   = PERSON
"Chen Dynasty pottery is on display"  → Chen Dynasty = ORG
```

Same token, different label. No regex can do that — not because the pattern is hard, but because
the answer is not in the pattern at all. This is what regex-only DLP structurally lacks.

### Microsoft Presidio
An open-source PII framework that wraps an NLP engine (spaCy here) and adds ~30 recognizers,
context-aware confidence adjustment, and consistent scoring. Runs fully local.

| Option | Verdict |
|---|---|
| spaCy alone | ✅ the engine, but you build scoring and PII recognizers yourself |
| **Presidio** | ✅ **chosen** — recognizers, context enhancer and confidence scores, local |
| Transformer NER | ⚠️ better accuracy, 100–500 ms — 10,000× our Stage 1 |
| Cloud PII APIs | ❌ self-defeating: a DLP tool that ships prompts to a third party *is* the leak |

**Two things measurement changed:**

- **The entity allowlist is a 10× speed-up**, not just a correctness rule. All ~30 recognizers
  cost 30–230 ms; restricting to four types gives mean 2.8 ms. Narrow scope turned out to be a
  performance decision as much as a double-counting one.
- **`en_core_web_md`, not `lg`.** They differ mainly in *word vectors*, not the NER component —
  published F-scores 0.84 / 0.85 / 0.85. `md` gives lg-equivalent recognition at 40 MB vs 560 MB.

⚠️ A bare `AnalyzerEngine()` silently downloads `en_core_web_lg` (400 MB) on first use, which
presents as an inexplicable multi-minute hang. Always configure `NlpEngineProvider` explicitly.

### Competing claims vs additive context
The distinction that decides how two detectors' overlapping findings are merged.

**Competing claims** — two detectors disagree about what the same characters *are* (an Aadhaar
number *or* a payment card, not both). Confidence is the right arbiter, because the question is
about truth. → [D16](decisions.md)

**Additive context** — a semantic label and a secret classification are *not* rivals. spaCy
labelling a random token as `PERSON` at 0.85 is simply wrong; our detector already knows what that
span is. Here the rule is precedence, not arbitration: **Stage 1 is authoritative on spans it has
already classified.** → [D26](decisions.md)

Getting this backwards cost five false negatives — a `low`-severity semantic guess displaced a
`medium`-severity secret and the message was allowed through.

### OCR (Optical Character Recognition)
Extracting text from images. The Stage 3 capability that lets ShadowGuard read a screenshot
containing an API key. Accuracy is won or lost in **preprocessing** (contrast, thresholding,
deskewing), not in the OCR engine.
