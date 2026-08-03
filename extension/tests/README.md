# Extension tests

```bash
npm install     # once — pulls jsdom
npm test        # 39 tests
```

Node's built-in runner (`node --test`) plus **jsdom**. No Jest, no Vitest, no build step: the
content scripts are classic scripts that attach to `globalThis.SG`, so giving them a `globalThis`
with a DOM on it loads them exactly as Chrome does.

## Why these exist

The Python side had **181 tests and none of the bugs**. The extension had **zero tests and all
three**:

| | Defect | Found by |
|---|---|---|
| [D38](../../docs/decisions.md) | `approve()` and `discard()` deleted, six call sites left behind | a human, in a browser |
| [D39](../../docs/decisions.md) | verification read the same layer it had just written to | a human, in a browser |
| [D39](../../docs/decisions.md) | the composer rewrite appended instead of replacing | a human, in a browser |

That is not a coincidence, and it is the argument for this directory. Test coverage that stops at a
language boundary leaves the far side unprotected however good the tested half looks.

**Every regression test here was verified by re-introducing its bug and watching it fail.** A
regression test that has never failed is an assertion about code, not a test of it.

## What is covered

| File | Covers |
|---|---|
| `adapter.test.js` | reading the composer, matching the send button, and the composer rewrite |
| `interceptor.test.js` | which sends are inspected, and — just as important — which are left alone |
| `modal.test.js` | the decision promise, and what the user is actually shown |
| `contract.test.js` | agreements nobody owns: Python ↔ JavaScript vocabularies, manifest ↔ disk |

`contract.test.js` is the one worth explaining. It parses `Action` out of the **Python** schema and
asserts `SG.ACTION` matches. Those four strings travel Python → service worker → content script →
UI, and the content script routes on them by string comparison. A mismatch raises nothing: the
message is neither sent nor blocked, and no component thinks anything went wrong.

## What is NOT covered, and cannot be

Stated plainly, because a test suite that overstates its reach is worse than a small honest one.

- **Top-layer rendering.** The modal uses `<dialog>.showModal()` specifically so the browser renders
  it above every stacking context on the page ([D9](../../docs/decisions.md)). JSDOM has no layout.
- **Real editor behaviour.** `fake_editor.js` models what ProseMirror was *measured* doing — it
  appends on a collapsed selection, and reverts direct DOM writes. It is a model, and a model that
  agrees with our assumptions would pass even when those assumptions are wrong. **That is exactly
  how four consecutive fixes to the composer rewrite were each verified and each wrong.**
- **Chrome's extension APIs.** `chrome.runtime` is a stand-in; service-worker lifecycle, message
  passing across worlds and `host_permissions` are not exercised.
- **ChatGPT's actual DOM.** Selectors are tested against fixtures we wrote. When ChatGPT ships a
  redesign these tests will keep passing and the extension will stop working — which is precisely
  why the adapter uses a tiered selector list and logs which tier matched.

**Browser verification remains a required manual step**, listed in
[docs/testing.md](../../docs/testing.md). These tests make it faster to trust a change; they do not
replace opening Chrome.

## The one test seam in production code

`Interceptor` accepts an `isTrusted` override. `event.isTrusted` is set by the browser and cannot
be forged — which is why it is the right guard in production and an impossible one to exercise in a
test. JSDOM defines it as a non-configurable own property, so every event a test can construct
looks synthetic, and *"a real Enter is intercepted"* could not be asserted at all.

Production uses the default, which reads the native flag. The override is reachable only by code
holding the instance, which lives in the extension's isolated world where no page script can reach
it.
