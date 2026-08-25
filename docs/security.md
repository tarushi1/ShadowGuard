# Security, privacy and ethics

A security tool that has not examined its own weaknesses is a security tool that has not been
examined. Finding holes in your own design is what separates a security project from a demo —
and volunteering them is far stronger than being asked.

---

## Threat model

**What this defends against:** a well-meaning employee who does not realise the text they are
about to paste contains a credential, customer record, or proprietary source. That is the
overwhelming majority of real data leakage to AI platforms — accident, not malice.

**What it does not defend against:** a determined insider. The extension is a **control on one
path**, not a perimeter. Anyone who wants to leak data can open a different browser, use the API
directly, or take a photo of the screen. Claiming otherwise would be dishonest.

This distinction is worth stating explicitly in the report, because an evaluator will ask.

---

## Known bypasses

Tracked deliberately. Each is either fixed, scheduled, or accepted with reasoning.

| # | Bypass | Status |
|---|---|---|
| B1 | **Edit-after-analysis.** The composer stays editable while a decision is pending, so a user can trip the warning with benign text, paste a secret while the modal is open, then click Proceed — sending text that was never inspected. | **Closed** — every replay path re-reads the composer and analyses the new text rather than sending it ([D52](decisions.md#d52--the-bytes-sent-are-the-bytes-inspected)). Bounded at three re-checks, failing closed. `approve()` additionally refuses outright |
| B2 | **Fail-open.** Killing the backend disables inspection. | **Accepted** with a visible notice — see [D12](decisions.md#d12--fail-open-but-visibly) |
| B3 | **`__sg` console handle.** `__sg.disable()` is reachable from DevTools. | Dev-only; would not ship in a real build |
| B4 | **Off-path usage.** The ChatGPT API, a different browser, a phone. | Inherent to the approach. State it plainly. |
| B5 | **Host page hostility.** ChatGPT could find and delete our modal's host element. `closed` shadow root stops it reaching *into* our UI but not removing it. | Inherent — a content script is a guest |
| B6 | **Client-supplied `user_id`.** Anyone can send any id, so behavioural profiles can be poisoned or evaded. | Prototype limitation; real deployment uses authenticated identity |
| B7 | **Uninstalling the extension.** | Enterprise deployments use forced-install policy; out of scope here |
| B8 | **Context starvation.** `INDIA_AADHAAR` and `INDIA_PAN` only fire when a supporting word appears within 50 characters ([D29](decisions.md)), so pasting the bare number with no surrounding words evades both. | **Accepted, and it is the price of the gate** — see below |
| B9 | **Edit during re-confirmation.** The composer stays editable while the redaction modal is open, so a user can approve `[REDACTED: CREDIT_CARD]` and have different text sent. A variant of B1, and worse: the approval was for text the user was explicitly shown. | Mitigated — `sendRedacted()` overwrites the composer rather than trusting it, then **re-reads and abandons the send** if the replacement did not take |
| B10 | **Redaction as an oracle.** A sender can learn exactly which spans are detected by watching what gets replaced, then rephrase until nothing is. | **Inherent to any visible control.** Warning and blocking leak the same information more slowly. Noted rather than mitigated |
| B14 | **Reloading the extension mid-session.** A tab open from before the reload keeps a dead `chrome.runtime` handle. It can still cancel sends but can never get a verdict. | **Fixed** — the rejection is caught, interception is switched off so the page stays usable, and the modal says to reload. Previously this failed CLOSED and silently |
| B15 | **A bare number beside a name.** `Priya Menon, 9876543210` is missed: the ambiguous phone form needs a nearby telephone word, and a name is not one. | **Accepted.** Closing it means treating a detected PERSON as context for a digit run, which crosses a stage boundary — a candidate for the risk engine rather than a pattern rule |
| B13 | **An unrecognised credential label.** The rule knows ~20 label words. `my sekrit is X`, `the passphrase for the vault, X`, or a label in another language is invisible. | **Inherent to a lexical rule.** Broadening the list costs precision — `key` and `pin` were removed after each caused a corpus false positive |
| B12 | **A credential with no label.** `PASSWORD` fires on the words around the secret, so `Hunter2Fake` sent alone, or after a label this rule does not know, is invisible. | **Inherent.** A password has no format; without a label there is nothing to detect. Same trade as B8 |
| B18 | **Drag the file in.** The attachment interceptor listened on `paste` and `change` only. Dragging a file from the desktop onto the composer fired neither, so no analysis ran and nothing was held — a total bypass of every stage, for images and documents alike. | **Closed.** A capture-phase `drop` listener, handled like `paste` because both are cancellable. Found by a user dropping a PDF containing an AWS key; the backend blocked that same file at score 92 when it was finally asked ([D54](decisions.md#d54--the-input-nobody-was-watching)) |
| B16 | **Scan the document.** A PDF with no text layer is a photograph in a wrapper. Stage 3b reads text and does not rasterise pages, so a scanned payslip yields nothing, scores zero and comes back `allow`. Print it, scan it, attach it — one step, defeats every rule in the system. | **Mitigated, not closed.** `has_text_layer` travels back with the verdict and the user is shown "this PDF has no text layer and has NOT been checked" rather than silence. The document is still allowed through under fail-open policy. Closing it properly needs page rasterisation ([D53](decisions.md#d53--pdfs-are-a-second-text-source-and-a-document-is-not-one-thing)) |
| B17 | **Put the secret in an embedded image.** A born-digital PDF with a perfect text layer can also carry a screenshot pasted into page three. Stage 3b extracts the text, finds nothing, and — because the text layer IS present — reports the document as read. | **Open.** Worse than B16 because `has_text_layer` is true, so the honest "we could not read this" warning does not fire. The document looks inspected and is only partly inspected. Named here rather than left to be discovered: it is the direct cost of choosing a pure-Python parser |
| B11 | **Screenshot a secret.** Detection through an image is measurably worse than on typed text — 93.9% retention on rendered images, and an upper bound at that. `GOOGLE_API_KEY` and `JWT` still survive OCR **0%** of the time, so screenshotting either is a reliable bypass today. | **Partially closed** by the repair step (D42), which took retention from 86.7%. The residue is measured and named rather than estimated. **Confirmed on a real screenshot 19 Aug 2026** — see below |

### On B11 — where the fragility actually lives

A real screenshot of `AKIAIOSFODNN7EXAMPLE` was allowed through, and the cause is sharper than
"OCR is unreliable". Rendering the same token in three fonts and reading it back:

```
Menlo      ->  AKIATOSFODNN7EXAMPLE     block    I->T, after the prefix
Helvetica  ->  AKIAIOSFODNN7EXAMPLE     block    exact
Times      ->  AKTAIOSFODNN7EXAMPLE     allow    I->T, inside the prefix
```

**Every font produced the same `I` -> `T` confusion.** What differs is *where* it lands. The rule
is `AKIA` plus sixteen characters: a substitution in the sixteen-character body is harmless,
because the wrong character is still a valid body character. A substitution inside the
four-character prefix destroys the match entirely.

**Four characters out of twenty carry all of the fragility.** That is the real shape of B11, and it
suggests the fix: match vendor prefixes with a small edit-distance tolerance on OCR-sourced text
only, where the blast radius is already contained (D42). Not attempted before Review-I.

It also confirms the caveat attached to every OCR figure in this project — measurements used
rendered monospace text, and a real screenshot in a proportional font does worse.

### On B8 — a bypass that was bought deliberately

B8 did not appear by accident; it was **created** by the fix in D29, and it is worth being precise
about the trade rather than presenting the fix as free.

Before the gate, those two rules fired on any twelve digits and on any five-letters-four-digits
token. That caught the bare-number case — and also caught badge numbers, order ids, phone ranges,
SKUs and part numbers. **A rule that fires on everything catches the leak and is switched off by
its users within a week**, which protects nothing at all. False positives are not merely annoying;
they are how a control loses the authority to stop anything.

So the trade is: give up the bare-number case, keep a rule people will still listen to.

Two properties make it defensible:

- **It is not an evasion an ordinary user stumbles into.** A person disclosing their own Aadhaar in
  a real question ("kyc document shows aadhaar …") writes the word. Reaching B8 means stripping
  the surrounding language on purpose — and B4 already concedes that a determined insider has far
  easier routes than that.
- **The failure is a miss, not a wrong answer.** The gate is an allowlist, so what it costs shows
  up in *recall*, which is measured and reported. A deny-phrase list would have hidden the same
  cost inside precision, where nothing would have shown it.

**Not closed by:** widening the window, which weakens the gate proportionally, or adding more
context words, which does nothing for text that has no words at all. The real closure is Week 5's
risk engine treating a bare unexplained identifier as a *weak* signal that can combine with others,
rather than the binary this stage can express — and, for Aadhaar specifically, the Verhoeff
checksum, which would supply the evidence the pattern lacks and make the gate unnecessary.

---

## Privacy properties

These are design commitments, not aspirations. Most have tests that fail if broken.

**The response never echoes the user's text.** Entities carry offsets into text the client
already holds. A DLP service that returns the secret it just found has doubled the number of
places that secret exists and put a copy in every HTTP log, proxy cache and error report between
the server and the browser. → [test](../backend/tests/test_analyze_endpoint.py)

**Logs record the decision, never the content.** Server logs are copied, shipped and retained far
more casually than databases. Logging prompts would recreate, in a plaintext file, exactly the
exposure this project exists to prevent. Length and entity types are enough to debug with.
→ [test](../backend/tests/test_analyze_endpoint.py)

**The database has no column that can hold a prompt.** The decision store records entity *types*,
offsets, scores, timings and the action taken — never a character anyone typed. `char_count`
replaces the text: enough to notice someone pasting 8000-character documents, useless for
reconstructing them.

This is structural rather than a rule. `record_decision()` takes the response, which the API
schema has already stripped, and a length computed by its caller, so it cannot store the text even
if someone tried. **A DLP tool's own database is the most concentrated collection of sensitive
prompts in an organisation if anyone lets it become one** — and "do not store prompt text" is one
convenient five-line change away from being false if it is only remembered.

Two tests, because each catches what the other misses: one writes a known secret through the real
endpoint and searches the database **file** for it — including the write-ahead log, where a secret
can sit while the main file looks clean — and one fails the moment a forbidden column *name*
appears, since the first would still pass on a column that exists but is not yet populated.
→ [test](../backend/tests/test_persistence.py)

**Retention is expressible, and not yet automatic.** `purge_older_than(days)` exists and cascades
correctly to entity rows. **Nothing calls it on a schedule.** Stated here rather than implied,
because a tool that keeps every decision forever becomes a permanent record of what each employee
was working on and when — built by the team that promised to reduce exposure. Scheduling it is
outstanding work, not a solved problem.

**The backend makes no external calls.** Everything runs on `127.0.0.1`. A DLP tool that ships
your secrets to a third-party API is self-defeating.

**`.gitignore` covers the sensitive artefacts.** The decision store holds no prompt text, but it
*is* a record of when each user was warned or blocked, which is local user data — the whole
`data/` directory is ignored, along with `-wal` and `-shm`, since SQLite buffers recent writes in
the write-ahead log. Screenshots captured during OCR testing may contain real secrets. A leaked
`.env` in a DLP project is an embarrassing story.

**Least privilege.** The extension started with **zero** permissions; `storage` and two localhost
hosts were added in the first task that needed them. Every permission carries a visible
install-time warning. The git history backs the claim up.

---

## Ethical considerations

**This is workplace surveillance.** The dashboard logs what people typed, when, and how often.
That is legitimate for protecting company data and also genuinely invasive. A serious deployment
would need:

- **Notice.** Employees told what is monitored, before it starts — not buried in a policy.
- **Proportionality.** Log the *verdict and entity types*, not the prompt. Our design already
  does this; it should be a stated policy, not an implementation accident.
- **Retention limits.** Events should expire. Indefinite retention of everything an employee
  typed into an AI tool is hard to justify.
- **Access control.** Who can read the dashboard, and is *that* audited?

**Chilling effects.** People who know their prompts are inspected use the tool less, including
for legitimate work. That cost is real and should be weighed rather than dismissed.

**False accusations.** A high risk score is not evidence of wrongdoing. If the dashboard becomes
an input to performance management, a false positive stops being a technical annoyance.

---

## The metric asymmetry

> **False negatives are the security failure. False positives are the adoption failure.**

A tool that cries wolf gets disabled — at which point its recall is zero and it protects nothing.
This is precisely why the Week 3 evaluation harness measures both, and why the semantic stage
exists at all: the improvement it must demonstrate is a **drop in false positives** at equal or
better recall versus regex alone. That delta is the single most important number in the report.

---

## Honest limitations

1. **Prototype identity.** `user_id` identifies a browser installation, not a person.
2. **Local-only.** Single user, single machine. Nothing about multi-tenancy or scale is solved.
3. **Selector fragility.** The extension depends on ChatGPT's DOM, which changes without notice.
   Mitigated by tiered selectors that log degradation *before* anything breaks — but mitigation
   is not immunity.
4. **English-centric detection.** Regex, entropy and spaCy models are tuned for English. Detection
   quality in other languages is untested.
5. **No encryption at rest.** The SQLite database holding event metadata is a plain file.
6. **OCR is best-effort.** Low-resolution or stylised screenshots will not extract cleanly, and a
   missed extraction is a silent false negative.
