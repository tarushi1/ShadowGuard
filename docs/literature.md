# Literature review, gap identification and objectives

*Report material. Written for Review-I (2026-08-19), rubric items 1–3.*

> **⚠️ CITATION CHECK REQUIRED BEFORE SUBMISSION.** Every reference below is one I believe to be
> real and correctly attributed, but **you must verify each one yourself** — open it, confirm the
> authors, venue and year, and confirm it says what this document claims it says. Items marked
> **[VERIFY]** are ones where I am less certain of the exact figure or numbering and you should be
> especially careful. A fabricated or misattributed citation in a graded report is far more damaging
> than a shorter reference list.

---

## 1. Rationale — why this problem, now

Generative AI assistants moved from novelty to daily tools in under two years, and they did so
**outside** the controls enterprises had built for every other data egress path. Email has DLP.
USB ports have policy. Cloud storage has CASB. A browser tab pointed at a chat assistant has
nothing.

The exposure is not hypothetical:

- **Samsung, April 2023.** Engineers pasted proprietary semiconductor source code and internal
  meeting notes into ChatGPT on at least three occasions within weeks of permitting its use. The
  company's response was to ban the tool outright, which is the blunt instrument this project
  exists to replace. *(Widely reported — Bloomberg, TechCrunch, April 2023.)* **[VERIFY]**
- **Regulatory response.** Italy's data protection authority (Garante) ordered a temporary halt to
  ChatGPT's processing of Italian users' data in March 2023, citing GDPR grounds including lawful
  basis and data-subject information. **[VERIFY]**
- **Measured workplace behaviour.** Commercial telemetry studies (e.g. Cyberhaven, 2023) reported
  that a meaningful fraction of material pasted into ChatGPT by employees was confidential.
  **[VERIFY the exact percentage before quoting it — commercial reports are not peer-reviewed and
  the figure has been widely misquoted.]**

Two properties make this worse than ordinary "data sent to a third party":

**Data that goes in can come back out.** Carlini et al. demonstrated that large language models
memorise and can be induced to reproduce verbatim training data, including personally identifiable
information [1]. Nasr et al. extended this to production systems, extracting training data from
deployed models at scale [2]. A pasted API key is not merely disclosed to a vendor — it may become
recoverable by unrelated parties.

**The disclosure is accidental, not malicious.** This is the crucial framing for the design. The
threat model is a well-meaning employee debugging a production issue who pastes a stack trace
containing a connection string — not an insider exfiltrating data. That distinction determines
everything: it justifies **warn-and-educate** over **block-everything**, and it makes false
positives an *adoption* failure rather than a nuisance, because a tool people switch off has a
recall of zero.

The OWASP Top 10 for Large Language Model Applications names *Sensitive Information Disclosure* as
a first-class risk category [3], but frames it primarily as the model disclosing information. The
inbound direction — the user disclosing to the model — is the less studied half and the one this
project addresses.

---

## 2. Prior art

### 2.1 Secret detection by pattern and entropy

The most directly relevant body of work is **secret scanning** for source repositories.

Meli, McNiece and Reaves conducted the largest empirical study of the problem, scanning a
substantial portion of public GitHub and finding secrets in a large number of repositories [4].
Methodologically, their approach is the ancestor of this project's Stage 1: **regular expressions
for vendor-specific formats**, combined with **Shannon entropy** [5] for keys whose format is not
known in advance, plus checksum validation to suppress false positives.

The production tools in this space share that architecture:

| Tool | Approach | Limitation for our problem |
|---|---|---|
| **TruffleHog** | Entropy + regex, with live credential verification | Scans repositories and files, not interactive input |
| **gitleaks** | Regex rule packs with allowlists | Pattern-only; no semantic layer |
| **detect-secrets** (Yelp) | Plugin architecture, entropy + heuristics | Designed for pre-commit hooks |

**What they establish:** pattern plus entropy is a sound, well-validated foundation for structured
secrets, and checksum validation materially improves precision.

**What they do not address:** all three operate on *files at rest* in a developer workflow. None
intercepts interactive input, none handles images, and none reasons about personal data — a
customer's name and address are invisible to every one of them.

### 2.2 PII detection by named-entity recognition

Personal data has no fixed format, so pattern matching cannot reach it. The established approach is
**Named Entity Recognition**: statistical models that label spans by what they refer to, using
surrounding context rather than token shape.

**Microsoft Presidio** [6] is the reference open-source implementation, combining an NER backbone
(commonly spaCy [7]) with pattern recognizers, checksum validators and context enhancers. It is
the component this project adopts for Stage 2.

**Known limitation, and one this project measures rather than inherits silently:** NER models
trained predominantly on Western news corpora perform measurably worse on non-Western names. This
is documented in the fairness literature on NER and demographic bias [8] **[VERIFY this citation
carefully — use a source you have read]**. Section 5 of this document states how the project
quantifies that effect rather than assuming it away.

### 2.3 Enterprise Data Loss Prevention

Commercial DLP (Forcepoint, Symantec/Broadcom, Microsoft Purview) operates at three points:
**endpoint** agents, **network** gateways, and **cloud API** integrations (CASB).

**Why none of them solves this.** A browser-based chat assistant is, at the network layer, an
ordinary HTTPS request to a permitted SaaS domain. Interception requires TLS termination — a
significant privacy and deployment cost — and even then the request body is a JSON blob whose
schema changes at the vendor's discretion. CASB integrations depend on APIs the AI vendors do not
uniformly expose. Endpoint DLP typically monitors file operations and clipboard events, not the
contents of a `contenteditable` div inside a single-page application.

The practical enterprise response has therefore been **blanket blocking** at the DNS or proxy
layer — the Samsung response — which destroys the productivity benefit and drives usage onto
unmonitored personal devices. This is the "shadow AI" problem the project's name refers to.

### 2.4 Browser-based interception

Browser extensions are an established vehicle for content inspection (password managers, ad
blockers, accessibility tools), and Manifest V3 provides the isolated-world content-script model
this project relies on. The academic literature on extension *security* is substantial; literature
on extensions as a **DLP enforcement point** for generative-AI interfaces is sparse, which is
itself part of the gap.

### 2.5 Multimodal leakage

Screenshots are a routine way developers share context — a terminal, a stack trace, a dashboard.
Optical character recognition (Tesseract [9]) makes their text machine-readable. To the best of the
literature surveyed, **no published DLP work evaluates detection quality through an OCR round
trip**, which this project treats as a measurable quantity rather than an assumed capability.

---

## 3. Gap identification

Synthesising the above, five gaps are identified. Each is specific, each is addressed by a stated
objective in §4, and each is measurable.

### G1 — No DLP control exists at the generative-AI input boundary

Existing DLP operates on files, networks and cloud APIs. The moment of exposure for shadow AI is a
keystroke in a browser composer, which no established control observes. The available response is
binary: allow the tool, or ban it.

### G2 — Existing secret detection is single-modality and single-technique

Repository scanners are pattern-and-entropy on text at rest. PII tools are NER on text. Neither
composes the two, and neither reads images. A message containing a customer name *and* an API key
*in a screenshot* is invisible to all of them.

### G3 — Detection is treated as classification, not as a decision

Prior tools output findings. A deployed control must output an **action**, and the mapping is not
trivial: a name alone is not a leak, a name plus an account number plus an email is a customer
record. No surveyed tool performs **weighted fusion across heterogeneous detectors** to reach a
graded decision.

### G4 — The false-positive cost is under-treated

Secret scanners run in CI, where a false positive costs a developer thirty seconds. An interactive
control that fires wrongly is *disabled*, and its recall becomes zero. **Precision is therefore a
security property here, not a usability preference** — an argument the repository-scanning
literature has little reason to make.

### G5 — Evaluation is rarely reported at the level a deployment needs

Where detection tools report numbers at all, they typically report recall on curated positives.
Missing are: precision corrected for **realistic base rates**, per-technique **ablation**,
**latency** in the user's interaction path, and **entity-level** as well as decision-level metrics.

---

## 4. Objective framing

The aim is stated once, then decomposed into objectives that are individually verifiable.

> **Aim.** To design, implement and *quantitatively evaluate* a context-aware, multimodal data-loss
> prevention framework that inspects enterprise input to generative-AI platforms **before**
> transmission, and reaches a graded decision — allow, warn, redact or block — with precision high
> enough to be tolerated in daily use.

| # | Objective | Verification |
|---|---|---|
| **O1** | Intercept every send path in a browser-based AI assistant, holding the message before transmission | All three paths (Enter, button, form submit) demonstrably cancelled and replayable |
| **O2** | Detect structured secrets by pattern with checksum and context validation | Per-rule precision/recall on a labelled corpus |
| **O3** | Detect secrets of *unknown* format by statistical means | Measured recall contribution of the entropy stage in isolation |
| **O4** | Detect personal data that has no format, using context | Measured contribution of the semantic stage, entity-level and decision-level |
| **O5** | Extend detection to images without storing them | OCR round-trip retention measured against known ground truth |
| **O6** | Fuse heterogeneous findings into a graded, explainable action | Measured recall gain from fusion alone, with no detector changed |
| **O7** | Achieve this at a latency invisible in the interaction path | p50/p95 measured, excluding and including transport |
| **O8** | Evaluate honestly, including where the system fails | Ablation table, base-rate-corrected precision, documented bypasses |

**Non-objectives**, stated to bound the scope and pre-empt the obvious criticism:

- Not a defence against a **determined insider**. This is a control on one path; a motivated actor
  can use a different browser, the vendor's API, or a phone camera. This is stated in the threat
  model rather than defended against.
- Not a claim of **completeness**. The bypass register (B1–B12) exists precisely because the
  honest position is a bounded control, not a perimeter.

---

## 5. How this work addresses each gap

*This section is the bridge from literature to implementation, and is the strongest material for
the Design/Methodology and Implementation marks.*

| Gap | Addressed by | Evidence |
|---|---|---|
| **G1** | Browser extension intercepting at capture phase, before the page's own handlers | All three send paths closed; measured end to end |
| **G2** | Four mechanisms composed in one pipeline: regex, entropy, NER, OCR | Ablation shows each is load-bearing (§6) |
| **G3** | A risk engine performing weighted fusion with combination bonuses and context amplification | **+8.6 recall points from fusion alone**, no detector changed |
| **G4** | Precision treated as a first-class metric; context gates and checksum validators added specifically to protect it | **0% false-positive rate** maintained across every stage addition |
| **G5** | A 210-sample labelled corpus, three-configuration ablation, Wilson confidence intervals, base-rate-corrected precision, entity-level metrics, and a **pre-registered** corpus extension | `evaluation/` — reproducible in one command |

### 6. Headline results as of Review-I

```
                     regex only    + entropy    + semantic
  recall                  78.1%        89.5%        98.1%
  false-positive rate      0.0%         0.0%         0.0%
  F1                      87.7%        94.5%        99.0%
  latency p95           0.029 ms     0.031 ms     3.840 ms

  images:  OCR round-trip retention 93.9%   ·   OCR-induced FPR 2.0%
```

**The single most defensible claim in the project:** each technique's contribution is measured
separately, on the same corpus, in one run — and the regex baseline was built deliberately
*strong* (18 rules, Luhn validation, context gates) so the comparison is not against a straw man.

---

## References

*Renumber and reformat to your department's required style. Verify every one.*

1. N. Carlini et al., "Extracting Training Data from Large Language Models," *USENIX Security
   Symposium*, 2021.
2. M. Nasr et al., "Scalable Extraction of Training Data from (Production) Language Models," 2023.
3. OWASP, *Top 10 for Large Language Model Applications*. **[VERIFY the current version and the
   item numbering — it was renumbered between the 2023 and 2025 lists.]**
4. M. Meli, M. R. McNiece and B. Reaves, "How Bad Can It Git? Characterizing Secret Leakage in
   Public GitHub Repositories," *NDSS*, 2019.
5. C. E. Shannon, "A Mathematical Theory of Communication," *Bell System Technical Journal*, 1948.
6. Microsoft, *Presidio: Data Protection and De-identification SDK*. Open-source project.
7. M. Honnibal and I. Montani, *spaCy: Industrial-Strength Natural Language Processing*.
8. Literature on demographic bias in named-entity recognition. **[VERIFY — cite a specific paper
   you have read; do not cite this line as written.]**
9. R. Smith, "An Overview of the Tesseract OCR Engine," *ICDAR*, 2007.
10. H. P. Luhn, "Computer for Verifying Numbers," US Patent 2,950,048, 1960.
11. Regulation (EU) 2016/679 (GDPR), Art. 4(1) *personal data*; Art. 5(1)(c) *data minimisation*.

**Tools referenced:** TruffleHog, gitleaks, detect-secrets (Yelp), Microsoft Purview, Forcepoint
DLP. Cite as software/documentation, not as papers.
