/**
 * Interceptor — deciding which sends get inspected.
 * ================================================================
 *
 * This is the component with the worst failure modes in the project,
 * in both directions:
 *
 *   too permissive  a send goes out uninspected — the leak this
 *                   project exists to prevent
 *   too aggressive  Shift+Enter stops inserting newlines, or an IME
 *                   user cannot type at all, and ChatGPT is broken
 *                   until the extension is uninstalled
 *
 * Neither shows up as an error. Both need tests that state the
 * intended behaviour rather than describing the implementation.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadLibraries } = require("./helpers/harness");

const PAGE = `<!doctype html><html><body>
  <form>
    <div id="prompt-textarea" contenteditable="true"></div>
    <button data-testid="send-button"><svg><path id="glyph"/></svg></button>
  </form>
  <input id="outside">
</body></html>`;

/**
 * Build an interceptor over a live page.
 *
 * `intercepted` records every call, so a test asserts on WHAT the
 * component decided rather than on how it decided it.
 */
/**
 * Build an interceptor over a live page.
 *
 * `isTrusted` is injected because it cannot be simulated. The browser
 * sets that flag and nothing in JavaScript can forge it — which is
 * precisely why production depends on it, and precisely why JSDOM
 * cannot produce an event that looks like a real keystroke. Every
 * event a test constructs reports `isTrusted === false`, so without
 * this seam the class's most important behaviour would be untestable.
 *
 * Tests mark an event as a replay by setting `event.__replay = true`,
 * mirroring what the browser would report for our own synthetic send.
 */
function setup({ text = "hello" } = {}) {
  const context = loadLibraries({ html: PAGE });
  const { window, SG } = context;

  const adapter = SG.resolveAdapter("chatgpt.com");
  const composer = window.document.getElementById("prompt-textarea");
  composer.textContent = text;

  const intercepted = [];
  const interceptor = new SG.Interceptor(
    adapter,
    () => composer,
    (body, info) => intercepted.push({ text: body, ...info }),
    { isTrusted: (event) => event.__replay !== true }
  );

  interceptor.start();

  return { ...context, adapter, composer, interceptor, intercepted };
}

/** A keydown as the browser would produce it, unless marked a replay. */
function pressEnter(window, target, overrides = {}) {
  const { replay = false, ...init } = overrides;

  const event = new window.KeyboardEvent("keydown", {
    key: "Enter",
    keyCode: 13,
    bubbles: true,
    cancelable: true,
    ...init,
  });

  event.__replay = replay;
  target.dispatchEvent(event);

  return event;
}

// ---------------------------------------------------------------
// The send paths that must be closed
// ---------------------------------------------------------------

test("Enter in the composer is intercepted and cancelled", () => {
  const { window, composer, intercepted } = setup({ text: "charge 4111111111111111" });

  const event = pressEnter(window, composer);

  assert.equal(intercepted.length, 1, "the send was not intercepted");
  assert.equal(intercepted[0].text, "charge 4111111111111111");
  assert.equal(event.defaultPrevented, true, "the page will still send this");
});

test("a click on the send button's child icon is intercepted", () => {
  // closest(), not matches(). Clicks land on the <path> inside the
  // <svg> inside the <button>, and a matches() check silently never
  // fires — the send goes through uninspected and nothing says so.
  const { window, intercepted } = setup();

  const glyph = window.document.getElementById("glyph");
  glyph.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));

  assert.equal(intercepted.length, 1, "a click on the send icon was not intercepted");
});

test("form submission is intercepted", () => {
  const { window, intercepted } = setup();

  const form = window.document.querySelector("form");
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));

  assert.equal(intercepted.length, 1, "the form submit path is open");
});

// ---------------------------------------------------------------
// What must pass through untouched
// ---------------------------------------------------------------

test("Shift+Enter inserts a newline and is not intercepted", () => {
  // Breaking this makes multi-line prompts impossible, which is the
  // fastest route to an uninstall.
  const { window, composer, intercepted } = setup();

  const event = pressEnter(window, composer, { shiftKey: true });

  assert.equal(intercepted.length, 0);
  assert.equal(event.defaultPrevented, false);
});

test("an IME composition keystroke is not intercepted", () => {
  // Pressing Enter to commit a candidate in Japanese, Chinese or
  // Korean input is not a send. Intercepting it makes the extension
  // unusable for a large fraction of the world, and an English-only
  // developer never sees it.
  const { window, composer, intercepted } = setup();

  pressEnter(window, composer, { isComposing: true });
  assert.equal(intercepted.length, 0, "isComposing was ignored");

  // Browsers disagree about which signal they set, so both are checked.
  pressEnter(window, composer, { keyCode: 229 });
  assert.equal(intercepted.length, 0, "the legacy keyCode 229 signal was ignored");
});

test("an empty composer is not intercepted", () => {
  // ProseMirror keeps a placeholder paragraph, so a naive length check
  // fires a backend analysis on every stray Enter.
  const { window, composer, intercepted } = setup({ text: "" });
  composer.innerHTML = "<p><br></p>";

  pressEnter(window, composer);

  assert.equal(intercepted.length, 0);
});

test("Enter outside the composer is not intercepted", () => {
  const { window, intercepted } = setup();

  pressEnter(window, window.document.getElementById("outside"));

  assert.equal(intercepted.length, 0, "Enter in an unrelated input was captured");
});

// ---------------------------------------------------------------
// The replay mechanism
// ---------------------------------------------------------------

test("a synthetic event is passed through, so an approved send can replay", () => {
  // The whole design rests on this. Approving a message replays the
  // send programmatically; if the interceptor caught its own replay,
  // the result would be an infinite loop of warnings.
  //
  // `isTrusted` is the guard because page script CANNOT forge it —
  // it needs no state flag of our own, and there is no window in which
  // a race could let a real send slip past as a replay.
  const { window, composer, intercepted } = setup();

  pressEnter(window, composer, { replay: true });

  assert.equal(intercepted.length, 0, "our own replay was intercepted — infinite loop");
});

test("disable() stops interception without unbinding listeners", () => {
  // Used constantly during development. If it unbound instead, enable()
  // would need to re-run discovery and the two paths would drift.
  const { window, composer, interceptor, intercepted } = setup();

  interceptor.enabled = false;
  pressEnter(window, composer);
  assert.equal(intercepted.length, 0);

  interceptor.enabled = true;
  pressEnter(window, composer);
  assert.equal(intercepted.length, 1, "enable() did not restore interception");
});

test("stats count what the guards actually did", () => {
  // These are the numbers a human reads to confirm the guards fire at
  // all. A counter that never moves is indistinguishable from a guard
  // that never runs.
  const { window, composer, interceptor } = setup();

  pressEnter(window, composer);
  pressEnter(window, composer, { shiftKey: true });

  assert.ok(interceptor.stats.seen >= 2, "keystrokes were not counted");
  assert.equal(interceptor.stats.intercepted, 1);
  assert.ok(interceptor.stats.passed >= 1);
});
