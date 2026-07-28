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
| B1 | **Edit-after-analysis.** The composer stays editable while a decision is pending, so a user can trip the warning with benign text, paste a secret while the modal is open, then click Proceed — sending text that was never inspected. | Detected and warned in `approve()`; becomes re-analysis in **Week 5** |
| B2 | **Fail-open.** Killing the backend disables inspection. | **Accepted** with a visible notice — see [D12](decisions.md#d12--fail-open-but-visibly) |
| B3 | **`__sg` console handle.** `__sg.disable()` is reachable from DevTools. | Dev-only; would not ship in a real build |
| B4 | **Off-path usage.** The ChatGPT API, a different browser, a phone. | Inherent to the approach. State it plainly. |
| B5 | **Host page hostility.** ChatGPT could find and delete our modal's host element. `closed` shadow root stops it reaching *into* our UI but not removing it. | Inherent — a content script is a guest |
| B6 | **Client-supplied `user_id`.** Anyone can send any id, so behavioural profiles can be poisoned or evaded. | Prototype limitation; real deployment uses authenticated identity |
| B7 | **Uninstalling the extension.** | Enterprise deployments use forced-install policy; out of scope here |

---

## Privacy properties

These are design commitments, not aspirations — two of them have tests that fail if broken.

**The response never echoes the user's text.** Entities carry offsets into text the client
already holds. A DLP service that returns the secret it just found has doubled the number of
places that secret exists and put a copy in every HTTP log, proxy cache and error report between
the server and the browser. → [test](../backend/tests/test_analyze_endpoint.py)

**Logs record the decision, never the content.** Server logs are copied, shipped and retained far
more casually than databases. Logging prompts would recreate, in a plaintext file, exactly the
exposure this project exists to prevent. Length and entity types are enough to debug with.
→ [test](../backend/tests/test_analyze_endpoint.py)

**The backend makes no external calls.** Everything runs on `127.0.0.1`. A DLP tool that ships
your secrets to a third-party API is self-defeating.

**`.gitignore` covers the sensitive artefacts.** The SQLite database will contain real logged
prompts; screenshots captured during OCR testing may contain real secrets. Both are ignored.
A leaked `.env` in a DLP project is an embarrassing story.

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
