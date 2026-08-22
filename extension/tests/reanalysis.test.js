/**
 * B1 — the text that was sent is the text that was inspected.
 * ================================================================
 *
 * The bypass these tests close is a time-of-check-to-time-of-use gap,
 * not a detection failure. ShadowGuard inspects the composer at one
 * moment and replays the send at another, and the composer stays
 * editable for the whole interval between them. So:
 *
 *   1. trip a warning with something harmless
 *   2. paste a secret while the dialog is open
 *   3. click Proceed
 *
 * and the secret goes out under an approval given for different text.
 * No detector improvement can close this, because no detector ever saw
 * the message.
 *
 * The old code DID notice — it called console.warn and sent anyway.
 * That is the specific thing these tests exist to stop coming back: a
 * check that reports a violation to a place nobody is looking is not a
 * mitigation.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { load, manifestScripts, settle } = require("./helpers/harness");

const BENIGN = "what is the weather in chennai";
const SECRET = "my aws key is AKIA3RTFPQ2XMBNZ7KLW";

const PAGE = `<!doctype html><html><body>
  <form>
    <div id="prompt-textarea" contenteditable="true">${BENIGN}</div>
    <button data-testid="send-button">send</button>
  </form>
</body></html>`;

/** A verdict shaped like the backend's, with only the fields the UI reads. */
function verdictFor(text) {
  const dirty = text.includes("AKIA");

  return {
    action: dirty ? "block" : text.includes("weather") ? "warn" : "allow",
    level: dirty ? "critical" : "medium",
    score: dirty ? 95 : 40,
    entities: dirty ? [{ type: "AWS_ACCESS_KEY_ID" }] : [],
    message: "test verdict",
    latency_ms: 1,
    request_id: "test",
  };
}

/**
 * Boot the extension with a scripted backend, recording two things:
 * every text that was ANALYSED, and every time a send was REPLAYED.
 */
function boot({ verdict = verdictFor } = {}) {
  const analysed = [];

  const context = load({
    html: PAGE,
    scripts: manifestScripts(),
    chrome: {
      onSendMessage: async (message) => {
        if (message?.payload?.text === undefined) return { ok: true };
        analysed.push(message.payload.text);
        return { ok: true, verdict: verdict(message.payload.text), roundTripMs: 1 };
      },
    },
  });

  // `isTrusted` is unforgeable in a real browser and always false for
  // anything the extension dispatches, which is exactly why it is the
  // right guard in production and an impossible one to reproduce here.
  //
  // The stand-in tags the events the TEST originates rather than the
  // ones the extension originates, and that direction matters. Marking
  // replays instead would leave `button.click()` — the primary send
  // path — looking like a genuine user click, so every successful send
  // would be re-intercepted and analysed a second time. That is a
  // harness artefact, but it would have been read as a real bug in the
  // re-analysis counting below.
  context.window.__sg.interceptor.isTrusted = (event) => event.__user === true;

  // The send path clicks the platform's own button, so counting clicks
  // on it counts sends. Asserting on this rather than on `pending` is
  // deliberate: `pending` is our own bookkeeping, and a test that
  // reads it would agree with the code about whether a message left.
  const sends = [];
  const button = context.window.document.querySelector('[data-testid="send-button"]');
  button.addEventListener("click", () => sends.push(read(context)));

  return { context, analysed, sends };
}

function composerOf(context) {
  return context.window.document.getElementById("prompt-textarea");
}

function read(context) {
  return composerOf(context).textContent;
}

/** Type something new into the composer, as a user would mid-dialog. */
function retype(context, text) {
  composerOf(context).textContent = text;
}

function pressEnter(context) {
  const event = new context.window.KeyboardEvent("keydown", {
    key: "Enter", code: "Enter", keyCode: 13, which: 13,
    bubbles: true, cancelable: true, composed: true,
  });
  event.__user = true;
  composerOf(context).dispatchEvent(event);
  return event;
}

/** Click a button inside the modal's closed shadow root. */
function clickModal(context, id) {
  const modal = context.window.__sg.modal;
  const button = modal.dialog?.querySelector(id);
  assert.ok(button, `no ${id} in the dialog — was one shown at all?`);
  button.dispatchEvent(
    new context.window.MouseEvent("click", { bubbles: true })
  );
}

// ---------------------------------------------------------------
// The attack
// ---------------------------------------------------------------

test("a secret pasted while the dialog is open is analysed, not sent", async () => {
  const { context, analysed, sends } = boot();

  pressEnter(context);
  await settle(context.window, 6);

  assert.deepEqual(analysed, [BENIGN], "the benign text should have been analysed first");

  // The attack: swap the contents while the warning is up.
  retype(context, SECRET);
  clickModal(context, "#sg-proceed");
  await settle(context.window, 6);

  assert.ok(
    analysed.includes(SECRET),
    `the edited text was never inspected — analysed: ${JSON.stringify(analysed)}`
  );
  assert.deepEqual(
    sends, [],
    "the edited text was SENT — B1 is open again"
  );
});

test("the refusal is visible, not just logged", async () => {
  // The precise regression. The original code performed this check
  // correctly and reported it with console.warn, which no user reads —
  // so the bypass worked in practice while looking handled in source.
  const { context, sends } = boot();

  pressEnter(context);
  await settle(context.window, 6);

  retype(context, SECRET);
  clickModal(context, "#sg-proceed");
  await settle(context.window, 6);

  const modal = context.window.__sg.modal;

  assert.equal(modal.isOpen, true, "nothing was shown to the user");
  assert.deepEqual(sends, [], "the message went out anyway");

  // The second dialog is about the SECRET, not about the weather.
  assert.match(
    modal.dialog.querySelector("#sg-entities").textContent,
    /AWS_ACCESS_KEY_ID/,
    "the new dialog does not describe the text that is actually in the box"
  );
});

// ---------------------------------------------------------------
// The innocent case, which must stay smooth
// ---------------------------------------------------------------

test("removing the flagged content and proceeding just sends", async () => {
  // The common case, and the reason this is re-analysis rather than a
  // flat refusal. The dialog says "there is something sensitive in
  // here", the user deletes it while the dialog is open and clicks
  // Proceed. A refusal would make the CORRECT action feel broken.
  const { context, analysed, sends } = boot();

  pressEnter(context);
  await settle(context.window, 6);

  retype(context, "how do i rotate a key in general terms");
  clickModal(context, "#sg-proceed");
  await settle(context.window, 8);

  assert.equal(analysed.length, 2, "the edited text was not re-analysed");
  assert.equal(sends.length, 1, "a clean message was not sent after re-analysis");
  assert.equal(
    context.window.__sg.modal.isOpen, false,
    "a clean re-analysis should not have stopped to ask anything"
  );
});

test("text that never changed is sent without a second analysis", async () => {
  // The cost control. Re-analysis must be triggered by an actual edit,
  // not by every approval — otherwise every warned send costs two HTTP
  // round trips and the latency budget doubles for no benefit.
  const { context, analysed, sends } = boot();

  pressEnter(context);
  await settle(context.window, 6);

  clickModal(context, "#sg-proceed");
  await settle(context.window, 6);

  assert.deepEqual(analysed, [BENIGN], "an unchanged message was analysed twice");
  assert.equal(sends.length, 1, "an approved, unchanged message was not sent");
});

// ---------------------------------------------------------------
// The bound
// ---------------------------------------------------------------

test("a composer that keeps changing is refused, and nothing is sent", async () => {
  // Re-analysis is a loop with a human in it, and a hostile page is
  // not obliged to be human. The bound must fail CLOSED: giving up and
  // replaying whatever is in the box would turn the rate limit into
  // the bypass it was protecting against.
  const { context, sends } = boot({
    verdict: () => ({
      action: "warn", level: "medium", score: 40,
      entities: [], message: "test", latency_ms: 1, request_id: "t",
    }),
  });

  // Every time the dialog closes, the page rewrites the composer.
  let round = 0;
  const rewrite = () => retype(context, `mutation ${++round}`);

  pressEnter(context);
  await settle(context.window, 6);

  for (let i = 0; i < 6 && context.window.__sg.modal.isOpen; i += 1) {
    rewrite();
    clickModal(context, "#sg-proceed");
    await settle(context.window, 6);
  }

  assert.deepEqual(sends, [], "an endlessly mutating composer got a message through");
  assert.ok(
    context.console.saw("error", "refusing to send"),
    `the loop was never bounded:\n${context.console.text()}`
  );
});

// ---------------------------------------------------------------
// Defence in depth
// ---------------------------------------------------------------

test("approve() refuses directly, without the guard in front of it", async () => {
  // approve() is reachable from the __sg debug handle and from any
  // call site added later. The invariant it protects — the bytes sent
  // are the bytes inspected — must not depend on every future caller
  // remembering to check first.
  const { context, sends } = boot();

  pressEnter(context);
  await settle(context.window, 6);

  retype(context, SECRET);

  assert.equal(
    context.window.__sg.approve(), false,
    "approve() replayed text that had changed since inspection"
  );
  assert.deepEqual(sends, [], "approve() sent uninspected text");
});
