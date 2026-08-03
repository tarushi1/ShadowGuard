/**
 * Modal — the decision UI.
 * ================================================================
 *
 * The last thing standing between a secret and a send. Its failure
 * modes are quiet: a promise that never resolves leaves the held
 * message in limbo forever, and a Proceed button that appears at
 * `block` severity turns a policy decision into a suggestion.
 *
 * NOT tested here: that the dialog renders above the page. That is
 * the browser's top layer, and it is the entire reason `<dialog>` was
 * chosen over a positioned div (D9). JSDOM has no layout, so it
 * stays a manual check in docs/testing.md. Claiming otherwise would be
 * the mistake this project already made once — verifying something by
 * reading the layer that cannot disagree with you.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadLibraries } = require("./helpers/harness");

/** The shadow root is closed, so the instance is the only handle. */
function openModal(verdict) {
  const context = loadLibraries();
  const modal = new context.SG.WarningModal();
  const decision = modal.show(verdict);

  return { ...context, modal, decision };
}

/** Click a button inside the closed shadow root, via the instance. */
function click(modal, id) {
  const button = modal.dialog.querySelector(id);
  assert.ok(button, `no ${id} button in the dialog`);
  button.dispatchEvent(new button.ownerDocument.defaultView.MouseEvent("click", { bubbles: true }));
}

// ---------------------------------------------------------------
// The decision promise
// ---------------------------------------------------------------

test("Cancel resolves the promise with cancel", async () => {
  const { modal, decision } = openModal({ level: "high", score: 70, entities: ["CREDIT_CARD"] });

  click(modal, "#sg-cancel");

  assert.equal(await decision, "cancel");
});

test("Proceed resolves the promise with proceed", async () => {
  const { modal, decision } = openModal({ level: "medium", score: 40, entities: ["PERSON"] });

  click(modal, "#sg-proceed");

  assert.equal(await decision, "proceed");
});

test("dismiss resolves rather than leaving the caller awaiting forever", async () => {
  // Called on SPA navigation, when the composer we would replay into
  // has gone. A dialog that closes without resolving leaves the held
  // message stuck and the content script waiting on a promise nothing
  // will ever settle.
  const { modal, decision } = openModal({ level: "low", score: 10, entities: [] });

  modal.dismiss();

  assert.equal(await decision, "cancel");
});

test("isOpen reports an outstanding decision", async () => {
  // The content script guards on this. showModal() throws on an
  // already-open dialog, and overwriting the pending message would
  // send the wrong text under an approval given for different text.
  const { modal, decision } = openModal({ level: "high", score: 70, entities: [] });

  assert.equal(modal.isOpen, true);
  click(modal, "#sg-cancel");
  await decision;
  assert.equal(modal.isOpen, false);
});

// ---------------------------------------------------------------
// What the user is shown
// ---------------------------------------------------------------

test("block severity offers no way through", async () => {
  // A policy decision surfaced in the UI: warn is advisory, block is
  // not. If this button is reachable, `block` silently becomes `warn`.
  const { modal, decision } = openModal({
    level: "critical",
    score: 96,
    entities: ["PRIVATE_KEY"],
    allowProceed: false,
  });

  assert.equal(modal.dialog.querySelector("#sg-proceed").hidden, true);

  modal.dismiss();
  await decision;
});

test("the redact variant previews what will be SENT, not what was typed", async () => {
  // Showing the original here would mean the user approves a message
  // they were never shown — which is the entire thing re-confirmation
  // exists to prevent (D35).
  const { modal, decision } = openModal({
    variant: "redact",
    level: "medium",
    score: 54,
    entities: ["CREDIT_CARD"],
    preview: "charge 4532015112830366 for the invoice",
    redactedText: "charge [REDACTED: CREDIT_CARD] for the invoice",
  });

  const shown = modal.dialog.querySelector("#sg-preview").textContent;

  assert.ok(shown.includes("[REDACTED: CREDIT_CARD]"), "the redacted text was not shown");
  assert.ok(!shown.includes("4532015112830366"), "the original card number was displayed");
  assert.equal(modal.dialog.querySelector("#sg-proceed").textContent, "Send redacted");

  modal.dismiss();
  await decision;
});

test("the offline variant does not show a score", async () => {
  // Nothing ran, so "0 / 100 (low)" would read as "we checked and it
  // is fine" — the opposite of the truth.
  const { modal, decision } = openModal({ variant: "offline", message: "not checked" });

  assert.equal(modal.dialog.querySelector("#sg-detail").hidden, true);

  modal.dismiss();
  await decision;
});

test("entity names are rendered as text, never as markup", async () => {
  // Entity types come from the backend. Injecting them with innerHTML
  // would put a server-controlled string into the DOM of a page we do
  // not own — and this modal exists to protect that page's user.
  const { modal, decision } = openModal({
    level: "high",
    score: 70,
    entities: ["<img src=x onerror=alert(1)>"],
  });

  const list = modal.dialog.querySelector("#sg-entities");

  assert.equal(list.querySelectorAll("img").length, 0, "markup from the server was rendered");
  assert.ok(list.textContent.includes("<img"), "the type name was lost entirely");

  modal.dismiss();
  await decision;
});

test("the safe button is focused by default for a risk warning", async () => {
  // Which button is focused IS the default action, because Enter
  // activates it. A user clearing a dialog by reflex must not send.
  const { modal, decision } = openModal({ level: "high", score: 70, entities: [] });

  assert.equal(modal._defaultButton?.id, "sg-cancel");

  modal.dismiss();
  await decision;
});

test("the redact variant defaults to its action button", async () => {
  // Deliberately inverted: the proposed send is strictly safer than
  // what the user typed, so defaulting to Cancel would make the safe
  // path the effortful one.
  const { modal, decision } = openModal({
    variant: "redact",
    level: "medium",
    score: 54,
    entities: ["CREDIT_CARD"],
    redactedText: "charge [REDACTED: CREDIT_CARD]",
  });

  assert.equal(modal._defaultButton?.id, "sg-proceed");

  modal.dismiss();
  await decision;
});
