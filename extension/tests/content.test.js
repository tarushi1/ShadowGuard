/**
 * The orchestrator — content.js, loaded as Chrome loads it.
 * ================================================================
 *
 * The other suites load every script EXCEPT content.js, because it
 * self-starts and needs a page that looks like ChatGPT. These tests
 * supply one, so the wiring between interceptor, worker and modal is
 * exercised rather than assumed.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { load, manifestScripts, settle } = require("./helpers/harness");

const PAGE = `<!doctype html><html><body>
  <form>
    <div id="prompt-textarea" contenteditable="true">my password is Hunter2Fake</div>
    <button data-testid="send-button">send</button>
  </form>
</body></html>`;

/** Everything, content.js included, with a scripted worker reply. */
function bootExtension({ onSendMessage }) {
  const context = load({ html: PAGE, scripts: manifestScripts(), chrome: { onSendMessage } });

  // `isTrusted` cannot be forged or redefined in JSDOM — which is
  // exactly why it is the right guard in production and an impossible
  // one to exercise in a test. content.js builds its own Interceptor,
  // so the seam is reached through the debug handle after the fact.
  context.window.__sg.interceptor.isTrusted = (event) => event.__replay !== true;

  return context;
}

function pressEnter(window) {
  const composer = window.document.getElementById("prompt-textarea");

  const event = new window.KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13,
    bubbles: true, cancelable: true, composed: true,
  });

  composer.dispatchEvent(event);
  return event;
}

// ---------------------------------------------------------------
// Extension context invalidated
// ---------------------------------------------------------------

test("a disconnected extension does not leave the composer stuck", async () => {
  // The failure a user hit the night before a demo.
  //
  // `chrome.runtime.sendMessage` REJECTS when the extension has been
  // reloaded while the page stayed open. The caller handled a failed
  // ANALYSIS but not a rejected PROMISE, so the exception escaped
  // `handleIntercept`: the send stayed cancelled and nothing was ever
  // replayed. The composer simply stopped working.
  //
  // That is failing CLOSED, silently — the opposite of the stated
  // policy in D12, reached by forgetting that a rejection is not a
  // return value.
  const context = bootExtension({
    onSendMessage: () => {
      throw new Error("Extension context invalidated.");
    },
  });

  pressEnter(context.window);
  await settle(context.window, 6);

  assert.ok(
    context.console.saw("warn", "DISCONNECTED"),
    `no disconnection notice was logged:\n${context.console.text()}`
  );
});

test("a disconnected extension stops intercepting so the page stays usable", async () => {
  // Nothing we send will ever be answered, so continuing to cancel
  // sends would hold every subsequent message for an analysis that
  // cannot arrive — the user could not use ChatGPT at all until they
  // reloaded, with only a console error to explain why.
  const context = bootExtension({
    onSendMessage: () => {
      throw new Error("Extension context invalidated.");
    },
  });

  pressEnter(context.window);
  await settle(context.window, 6);

  // Deliberately NOT asserting that `pending` is cleared. It is meant
  // to be held until the user answers the modal, and JSDOM cannot
  // resolve `showModal()` — so an assertion on it would be testing the
  // harness rather than the code.
  //
  // The second keystroke reaching the page untouched is the property
  // that actually matters: it is what keeps the composer usable.
  const second = pressEnter(context.window);
  assert.equal(
    second.defaultPrevented, false,
    "still cancelling sends after disconnection — the composer is bricked"
  );
});

test("an ordinary backend failure still shows the offline modal", async () => {
  // The disconnection path must not swallow the case it was modelled
  // on. A worker that answers `{ok: false}` is a DIFFERENT failure —
  // the extension is fine, the service is down — and it keeps the
  // wording and behaviour it had.
  const context = bootExtension({
    onSendMessage: async () => ({ ok: false, error: "connection refused" }),
  });

  pressEnter(context.window);
  await settle(context.window, 6);

  assert.ok(
    context.console.saw("warn", "could not reach the backend"),
    `the offline path did not run:\n${context.console.text()}`
  );
  assert.ok(
    !context.console.saw("warn", "DISCONNECTED"),
    "a backend outage was misreported as a disconnected extension"
  );
});
