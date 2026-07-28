# ShadowGuard AI — Knowledge Base

Everything needed to understand, defend, run and extend this project. Written so that
someone who has never seen the codebase — a teammate, an evaluator, or you in six weeks —
can get oriented without reading the source.

> [`../README.md`](../README.md) is the **session-recovery document**: current state, next task,
> roadmap. This folder is the **explanatory** layer: how things work and why they were built
> this way. Both are kept current as work proceeds.

## Reading order

| # | Document | What it covers |
|---|---|---|
| 1 | [architecture.md](architecture.md) | The system as a whole. Components, data flow, why the boundaries sit where they do. **Start here.** |
| 2 | [concepts.md](concepts.md) | Every technical concept the project uses, explained from zero. Browser internals, Python tooling, evaluation metrics. |
| 3 | [decisions.md](decisions.md) | The decision log. Each significant choice, the alternatives, and the reasoning. This is what "defend your design" questions draw on. |
| 4 | [api.md](api.md) | The HTTP contract between extension and backend. |
| 5 | [security.md](security.md) | Threat model, known bypasses, privacy, ethics, limitations. |
| 6 | [testing.md](testing.md) | How to verify every component, and what "working" looks like. |

## The one-paragraph version

Employees paste secrets into ChatGPT. Existing DLP tools use regex and keyword matching, which
produce false positives, understand no context, and cannot read screenshots. ShadowGuard sits
between the user and the AI platform as a Chrome extension: it **cancels** the send, ships the
content to a local Python service that runs layered detection (regex + entropy → semantic NER →
OCR → behavioural baseline → risk fusion), and **replays** the send only if policy allows.
The browser detects and enforces; the server decides.

## Project layout

```
ShadowGuard/
├── README.md          state: what is built, what is next
├── docs/              this knowledge base
├── extension/         Stage 0 — the Chrome extension (MV3, plain JS)
├── backend/           Stages 1-5 — FastAPI, detectors, risk engine
├── evaluation/        labelled corpus + metrics harness
├── dashboard/         admin UI (Week 7)
└── scripts/           setup helpers
```
