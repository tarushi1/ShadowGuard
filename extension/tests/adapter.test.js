/**
 * Adapter — reading, matching and rewriting the composer.
 * ================================================================
 *
 * The adapter is the only file allowed to know site-specific details,
 * which makes it the only file that can break when ChatGPT ships a
 * redesign. It is also where two of the three defects found in the
 * browser lived.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadLibraries, settle } = require("./helpers/harness");
const { attachFakeEditor } = require("./helpers/fake_editor");

function withComposer(html) {
  const context = loadLibraries({
    html: `<!doctype html><html><body>${html}</body></html>`,
  });
  const adapter = context.SG.resolveAdapter("chatgpt.com");

  return { ...context, adapter };
}

// ---------------------------------------------------------------
// Registry
// ---------------------------------------------------------------

test("the adapter is registered for the hosts the manifest matches", () => {
  const { SG } = loadLibraries();

  assert.ok(SG.resolveAdapter("chatgpt.com"), "chatgpt.com has no adapter");
  assert.equal(SG.resolveAdapter("example.com"), null);
});

// ---------------------------------------------------------------
// Reading
// ---------------------------------------------------------------

test("readText uses innerText, not textContent, for contenteditable", () => {
  // A detection-accuracy decision, not a formatting one. textContent
  // fuses adjacent blocks, so a key split across two lines reads as one
  // unbroken token and matches a pattern it should not.
  const { window, adapter } = withComposer(
    `<div id="prompt-textarea" contenteditable="true"><p>sk-proj-abc</p><p>def123</p></div>`
  );
  const composer = window.document.getElementById("prompt-textarea");

  assert.equal(composer.textContent, "sk-proj-abcdef123", "precondition");
  assert.ok(adapter.readText(composer).includes("\n"), "the block boundary was lost");
});

test("readText prefers .value on a textarea", () => {
  const { window, adapter } = withComposer(`<textarea id="t">typed</textarea>`);
  const composer = window.document.getElementById("t");
  composer.value = "typed";

  assert.equal(adapter.readText(composer), "typed");
});

test("isEmpty ignores ProseMirror's placeholder paragraph", () => {
  // ProseMirror never leaves the editor truly blank — it keeps a
  // <p><br></p> so the caret has somewhere to live. A naive
  // `length > 0` check reports "the user typed something" on an empty
  // box, which would fire a backend call on every focus.
  const { window, adapter } = withComposer(
    `<div id="c" contenteditable="true"><p><br></p></div>`
  );

  assert.equal(adapter.isEmpty(window.document.getElementById("c")), true);
});

// ---------------------------------------------------------------
// Send button
// ---------------------------------------------------------------

test("matchSendButton finds the button from a click on its child svg path", () => {
  // `matches()` fails here and `closest()` succeeds. Clicks land on the
  // <path> inside the <svg> inside the <button>, so a matches() check
  // silently never fires and the send goes through uninspected.
  const { window, adapter } = withComposer(
    `<button data-testid="send-button"><svg><path id="glyph" d="M0 0"/></svg></button>`
  );

  const match = adapter.matchSendButton(window.document.getElementById("glyph"));

  assert.ok(match, "a click on the icon path was not recognised as a send");
});

test("findSendButton refuses a disabled button", () => {
  const { window, adapter } = withComposer(
    `<button data-testid="send-button" disabled></button>`
  );

  assert.equal(adapter.findSendButton(), null, "replaying into a disabled button no-ops silently");
});

// ---------------------------------------------------------------
// replaceText — where the redaction bug lived
// ---------------------------------------------------------------

test("replaceText replaces the whole composer, it does not append", async () => {
  // REGRESSION, D39. Three consecutive fixes produced this instead:
  //
  //   charge 4532015112830366 for the invoice please
  //   charge [REDACTED: CREDIT_CARD] for the invoice please
  //   charge [REDACTED: CREDIT_CARD] for the invoice please
  //
  // The original survived at the front because the editor never
  // learned about the selection and inserted at a collapsed cursor.
  const { window, adapter } = withComposer(`<div id="c" contenteditable="true"></div>`);
  const composer = window.document.getElementById("c");
  const editor = attachFakeEditor(window, composer, "charge 4532015112830366 for the invoice please");

  await adapter.replaceText(composer, "charge [REDACTED: CREDIT_CARD] for the invoice please");
  await settle(window);

  assert.equal(editor.model, "charge [REDACTED: CREDIT_CARD] for the invoice please");
  assert.ok(!editor.model.includes("4532015112830366"), "the card number survived");
});

test("replaceText reports how much text the selection covered", async () => {
  // The one number that diagnosed the append bug, after three fixes
  // had been attempted without measuring it. A selection of 0 means
  // the editor is about to insert at a cursor rather than replace.
  const original = "charge 4532015112830366 for the invoice please";
  const { window, adapter } = withComposer(`<div id="c" contenteditable="true"></div>`);
  const composer = window.document.getElementById("c");
  attachFakeEditor(window, composer, original);

  const result = await adapter.replaceText(composer, "[REDACTED: CREDIT_CARD]");

  // Derived from the string rather than hardcoded. A literal here
  // would need editing whenever the fixture changes, and the number
  // that matters is "all of it", not "46".
  assert.equal(result.selected, original.length, "the selection did not cover the existing text");
  assert.equal(result.ok, true);
});

test("replaceText goes through the paste path, not a DOM write", async () => {
  // Assigning textContent would satisfy any check that reads the DOM
  // while leaving the editor's model untouched — the box shows the
  // redaction and the app sends the original. There is deliberately no
  // such fallback; this asserts the input path was genuinely used.
  const { window, adapter } = withComposer(`<div id="c" contenteditable="true"></div>`);
  const composer = window.document.getElementById("c");
  const editor = attachFakeEditor(window, composer, "some original text");

  await adapter.replaceText(composer, "[REDACTED: X]");

  assert.equal(editor.pastes, 1, "the editor never received a paste");
});

test("replaceText reports failure when the editor ignores it", async () => {
  // An editor our selection nudge cannot reach — the state every
  // failing version was in. The contract that matters is that this is
  // REPORTED rather than papered over, because the caller must be able
  // to abandon the send.
  const { window, adapter } = withComposer(`<div id="c" contenteditable="true"></div>`);
  const composer = window.document.getElementById("c");
  const editor = attachFakeEditor(window, composer, "charge 4532015112830366 please", {
    syncSelection: false,
  });

  const result = await adapter.replaceText(composer, "charge [REDACTED: CREDIT_CARD] please");
  await settle(window);

  assert.equal(result.ok, false, "a failed rewrite reported success");
  assert.ok(editor.model.includes("4532015112830366"), "precondition: the editor kept the original");
});

test("replaceText on a React textarea uses the native value setter", async () => {
  // `element.value = x` updates the DOM node but not React's state, and
  // React deduplicates against its own last known value so onChange
  // never fires. The box shows the new text and the form submits the
  // old.
  const { window, adapter } = withComposer(`<textarea id="t"></textarea>`);
  const composer = window.document.getElementById("t");
  composer.value = "charge 4532015112830366";

  const events = [];
  composer.addEventListener("input", () => events.push("input"));

  const result = await adapter.replaceText(composer, "charge [REDACTED: CREDIT_CARD]");

  assert.equal(result.ok, true);
  assert.equal(composer.value, "charge [REDACTED: CREDIT_CARD]");
  assert.deepEqual(events, ["input"], "React never heard about the change");
});
