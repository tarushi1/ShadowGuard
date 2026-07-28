# API contract

Base URL: `http://127.0.0.1:8000` · Interactive docs: `/docs` · Spec: `/openapi.json`

The Pydantic models in [`backend/app/schemas/analyze.py`](../backend/app/schemas/analyze.py) **are**
the specification — validation, the OpenAPI document and the `/docs` form all derive from them, so
this page cannot drift from the code without the code changing first.

---

## `GET /health`

Liveness probe. Deliberately dependency-free: it must answer even when model files are missing or
the database is unreachable, so *"the process is down"* stays distinguishable from *"the process
is up but broken"*. Collapsing those two into one signal is how you spend an hour debugging the
wrong layer.

```json
{ "status": "ok", "service": "shadowguard-ai", "version": "0.1.0" }
```

---

## `POST /api/v1/analyze`

Inspect a message the user is about to send, and rule on it.

This call sits **directly in the user's path** — the extension has already cancelled the send and
is holding the message, so every millisecond here is a millisecond spent staring at a composer
that has not responded.

### Request

| Field | Type | Required | Notes |
|---|---|---|---|
| `text` | string | ✅ | Max 100,000 chars. The cap is a DoS control: without it one request can pin the CPU inside a regex or spaCy for minutes. |
| `user_id` | string | ✅ | **Client-supplied and therefore untrustworthy** — anyone can put any value here. Acceptable for a prototype; a real deployment takes identity from an authenticated session. |
| `platform` | string | ✅ | `chatgpt`, `gemini` |
| `trigger` | string | | `enter-key`, `send-button`, `form-submit` |
| `client_version` | string | | Logged so a version mismatch appears as a log line rather than mysterious behaviour |
| `image_b64` | string | | Base64 image for OCR. Unused until Week 6. |

```json
{
  "text": "my key is sk-proj-abc123def456",
  "user_id": "9f2c1a44-...",
  "platform": "chatgpt",
  "trigger": "enter-key",
  "client_version": "0.8.0"
}
```

### Response

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Correlation id. The same value appears in the server log, the SQLite event row and the extension console — one incident, traceable across three systems. |
| `action` | enum | `allow` · `warn` · `redact` · `block` |
| `level` | enum | `low` · `medium` · `high` · `critical` |
| `score` | int | 0–100, fused risk |
| `entities` | array | See below |
| `message` | string | Human-readable text shown in the modal |
| `redacted_text` | string? | Only for `action=redact` (Week 5) |
| `latency_ms` | float | Server-side processing time |
| `stages_run` | string[] | Which stages actually executed |

Each entity:

```json
{ "type": "OPENAI_API_KEY", "start": 10, "end": 34, "score": 0.99, "detector": "regex" }
```

```json
{
  "request_id": "1ebfbf73d282",
  "action": "block",
  "level": "critical",
  "score": 95,
  "entities": [{ "type": "STUB_SECRET", "start": 10, "end": 13, "score": 1.0, "detector": "stub" }],
  "message": "A credential was detected in this message. Sending has been blocked.",
  "redacted_text": null,
  "latency_ms": 0.027,
  "stages_run": ["stub"]
}
```

### Entity types currently detected (Stage 1a)

| Type | Severity | Confidence | Notes |
|---|---|---|---|
| `PRIVATE_KEY` | critical | 1.00 | PEM / OpenSSH header — nothing else looks like it |
| `ANTHROPIC_API_KEY` | critical | 0.99 | |
| `GITHUB_TOKEN` / `GITHUB_PAT` | critical | 0.99 | |
| `STRIPE_SECRET_KEY` | critical | 0.99 | `sk_live_` only; `sk_test_` deliberately ignored |
| `OPENAI_API_KEY` | critical | 0.98 | incl. `sk-proj-`, `sk-svcacct-` |
| `AWS_ACCESS_KEY_ID` | critical | 0.97 | `AKIA` long-lived, `ASIA` session |
| `SLACK_TOKEN` | critical | 0.97 | |
| `US_SSN` | critical | 0.90 | never-issued ranges excluded |
| `INDIA_AADHAAR` | critical | 0.70 | Verhoeff checksum not yet implemented — known precision gap |
| `GOOGLE_API_KEY` | high | 0.96 | |
| `JWT` | high | 0.95 | |
| `CREDIT_CARD` | high | 0.90 | **Luhn-validated** |
| `INDIA_PAN` | high | 0.85 | |
| `IBAN` | high | 0.80 | mod-97 checksum not yet implemented |
| `EMAIL` | low | 0.95 | not a secret alone; matters in combination |

Measured latency: **~0.04 ms** per message end to end.

### Three properties worth understanding

**Offsets, never values.** `entities` carries positions into text the client already has. The
response never contains the secret — see [D10](decisions.md#d10--the-response-never-echoes-the-users-text).

**`latency_ms` and `stages_run` ship from day one.** Latency is a graded metric and cannot be
collected retrospectively. `stages_run` makes short-circuiting *visible evidence* rather than a
claim: when Stage 1 finds a critical secret the pipeline skips Stage 2, and this field proves it.

**`action` vs `level` are separate on purpose.** Severity is a property of what was *found*;
action is a property of *policy*. An organisation might warn rather than block on `critical`, and
that should be a config change, not a code change.

### Errors

| Status | When |
|---|---|
| `422` | Validation failure. Body lists each bad field with its location — generated by Pydantic, not written by hand. |
| `500` | Unhandled server error |

```json
{ "detail": [{ "type": "missing", "loc": ["body", "user_id"], "msg": "Field required" }] }
```

The extension treats **any** non-2xx, plus network failure and timeout, as *backend unavailable*
and applies the fail-open-visibly policy ([D12](decisions.md#d12--fail-open-but-visibly)).

---

## Client behaviour

| Situation | Extension does |
|---|---|
| `action: allow` | Replays the send silently. The user never learns ShadowGuard was there. |
| `action: warn` | Shows the modal with a **Send anyway** override, Cancel focused. |
| `action: block` | Shows the modal with **no override button**. |
| `action: redact` | Week 5 — replaces text with `redacted_text`, then sends. |
| Unreachable / timeout / 5xx | Grey "offline, not inspected" modal, sending focused. |

Timeout is **3000 ms** (`SG.CONFIG.REQUEST_TIMEOUT_MS`). `fetch()` has no default timeout, so
without `AbortController` a hung backend would freeze the send permanently.

---

## Versioning

`/api/v1/` exists because the extension and backend deploy **independently** — someone will run
an old extension against a new backend. Breaking changes go to `/api/v2/` while `/api/v1/` keeps
old clients alive. Without the version segment the only options are "break everyone" or "never
change anything".
