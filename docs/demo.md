# Demo script — Review-I

**Wednesday 19 August 2026 · SJT · 11:20–13:00**

Seven minutes, rehearsed. Every line below has been verified to produce the stated output. The
order is deliberate: each step demonstrates something the previous step could not do, which is the
ablation argument told as a story rather than a table.

---

## Before you leave the room

Run this. If anything fails, you have time to fix it; in the exam room you do not.

```bash
cd ~/Desktop/ShadowGuard
.venv/bin/pytest -q                      # expect: 239 passed
npm test                                 # expect: # pass 64
.venv/bin/python -m evaluation.run_eval  # expect: recall 98.1%, FPR 0.0%
```

**Then:**

1. `chrome://extensions` → **↻** on ShadowGuard → confirm **v0.11.1**
2. Open `chatgpt.com`, press **Cmd+R**, open the console (**⌥⌘I**)
3. Confirm the green `ACTIVE` badge and `composer matched at tier 1/6`
4. Start the backend and **leave the terminal visible** — the log lines are evidence:
   ```bash
   .venv/bin/uvicorn backend.app.main:app --reload
   ```

**Have a second terminal ready** with the evaluation output already scrolled to the comparison
table, in case the live demo misbehaves and you need to fall back to numbers.

---

## The demo, in order

### 1. It gets out of the way (20 seconds)

Type and send:

```
how do I reverse a string in python
```

> "Nothing happens. That is the most important behaviour in the system — if every message raised a
> dialog, the tool would be uninstalled by Friday and its recall would be zero."

Point at the server log: `action=allow`. **Every message is inspected**, including this one.

---

### 2. Pattern detection with validation (40 seconds)

```
charge 4111 1111 1111 1112 for the invoice
```

Nothing. Then change the last digit:

```
charge 4111 1111 1111 1111 for the invoice
```

Modal appears.

> "One digit apart. The first fails the **Luhn checksum**, so it is an order number. Sixteen digits
> alone would flag every invoice ID in the company — the validator cuts that rule's false positives
> by roughly an order of magnitude."

---

### 3. The unknown-format problem (40 seconds)

```
internal auth token Zq7Wm2Kp9Xr4Nv6Bt8Lc3Hd5Fj1Gy0Ae
```

> "This matches **no pattern in the system, and never will** — it is not a published vendor format.
> It is caught because it is *statistically random*: Shannon entropy over the character
> distribution. Pattern matching cannot express 'this looks like it was generated'."

---

### 4. The distinction the whole project rests on (60 seconds — **the money shot**)

Send:

```
explain how Satya Nadella restructured Microsoft around cloud services
```

Nothing happens.

Then:

```
our client Deborah Alvarez is threatening to sue over the denied claim
```

Modal, score 40.

> "Same entity types — a PERSON and an ORGANIZATION. Same severities. **Opposite verdicts.**
>
> The difference is not the token, it is the relationship language around it: *our client*. One is a
> person who is our **data subject**; the other is a person who is our **subject matter**.
>
> Counting findings would flag both. I measured that: warning on two-or-more findings catches 8 of
> 11 misses and costs **13 false positives**, including the Satya Nadella example. That is why the
> risk engine weighs evidence instead of counting it."

---

### 5. Redaction (45 seconds)

```
charge 4532015112830366 for the invoice please
```

> "Here the modal offers something different — **Send redacted**."

Show the preview, then click it. Point at the sent message:

```
charge [REDACTED: CREDIT_CARD] for the invoice please
```

> "The sentence survives; the card does not. And note it is a **typed placeholder**, not
> `4532 **** **** 0366`. Partial masking is familiar from banking UIs and wrong here — the issuer
> prefix plus the last four digits identifies a card in most customer-service systems. That is
> disclosure wearing redaction's clothes."

**If asked why it warns rather than redacting on some inputs:** redaction is offered only when it
*resolves* the risk. If the secret is most of the message, removing it leaves a receipt rather than
a message — so the answer is block.

---

### 6. Multimodal (45 seconds)

Have a screenshot ready **on the clipboard already** — a terminal window showing
`AKIAIOSFODNN7EXAMPLE`. Paste it.

> "The image is held *at attach time*, not at send. That matters: ChatGPT uploads an attachment to
> its own servers immediately, so by send time the bytes have already left the machine and we would
> be warning about a leak that had already happened."

Point at the modal preview:

> "It reports `1200×400, 14 words read at 91% confidence` — **what was read, never what it said**.
> Putting the extracted text on screen would display the secret we just found, on a page we do not
> own. And nothing is stored: not the image, not the text."

---

### 7. The credential gap (30 seconds — optional, but it lands well)

```
my password is Tr0ub4dorFake
```

> "A password has **no format**. It looks exactly like a username or a filename, and it is too short
> for entropy. The only evidence is the label beside it.
>
> This gap survived six weeks, 231 tests and a corpus reporting 98% recall — because all of those
> measured the categories I had thought of. It was found by using the tool for real. That is in the
> report as a limitation of corpus-driven evaluation."

---

## If something breaks

| Symptom | Say this, then move on |
|---|---|
| Grey "offline" modal | "The backend is down — and note it **fails open with a visible notice**, which is a documented decision, not an accident." Restart uvicorn. |
| No modal at all | Check the console context is **ShadowGuard AI**, not `top`. If still nothing, switch to the evaluation output. |
| ChatGPT layout changed | "The adapter matched at tier 4 rather than 1 — the tiering exists exactly for this, and the console reports it." Then switch to numbers. |
| Anything else | **Go to the second terminal.** `python -m evaluation.run_eval` proves the system works without a browser. |

**Rehearse the fallback.** The evaluation harness is a complete demonstration on its own, and a
candidate who pivots smoothly looks more competent than one whose demo never breaks.

---

## The three sentences to have ready

If time runs short, these carry the project:

1. **"Regex alone gets 78% recall. Entropy adds 11 points, semantics adds 9 more, and none of them
   costs a single false positive — measured on the same 210 samples in one run."**

2. **"Eight of eleven missed messages had *completely correct* detection. They were allowed through
   by policy, not by failure to detect — which is why the risk engine exists and why it is measured
   separately."**

3. **"I keep a register of twelve ways to bypass my own system, several of which my design choices
   created deliberately, with the trade-off stated for each."**
