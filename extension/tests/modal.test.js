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

// ---------------------------------------------------------------
// Theming — a warning nobody can read is a failure to warn
// ---------------------------------------------------------------

const fsMod = require("node:fs");
const pathMod = require("node:path");

function modalStyles() {
  return fsMod.readFileSync(
    pathMod.join(__dirname, "..", "src", "ui", "modal.js"), "utf8"
  );
}

test("the dark theme overrides only custom properties", () => {
  // The regression this test exists for.
  //
  // The first version set `.detail { background: #2a2a2e }` INSIDE
  // `@media (prefers-color-scheme: dark)`, near the top of the sheet,
  // with `.detail { background: #f4f4f6 }` further down. A media query
  // adds no specificity, so the later rule won and dark mode rendered
  // light text on a light panel.
  //
  // The check is that the dark block contains NO class selectors at
  // all — only `:host`, only tokens. An earlier version of this test
  // parsed declarations line by line and was verified by injecting the
  // bug: it passed, because the original defect was written inline and
  // the regex only saw properties at the start of a line. A regression
  // test that cannot fail on the exact bug it names is decoration.
  const styles = modalStyles();
  const queryAt = styles.indexOf("prefers-color-scheme: dark");
  const dark = styles.slice(styles.indexOf("\n", queryAt));
  const block = dark.slice(0, dark.indexOf("\n      }\n") + 10);

  const selectors = [...block.matchAll(/(^|\s)(\.[a-zA-Z][\w-]*)\s*(?=[,{])/g)]
    .map((match) => match[2]);

  assert.deepEqual(
    selectors, [],
    `dark mode styles ${selectors.join(", ")} directly; it must only override tokens on :host`
  );
});

test("no component rule hardcodes a surface or text colour", () => {
  // Every themeable colour has to come from a token, or one branch of
  // the theme silently keeps a value meant for the other.
  const styles = modalStyles();
  const cssStart = styles.indexOf("style.textContent = `");
  const css = styles.slice(cssStart, styles.indexOf("`;", cssStart));

  for (const selector of [".card", ".detail", ".preview", ".entity", ".btn-secondary"]) {
    const start = css.indexOf(`\n      ${selector} `) + 1;
    assert.ok(start > 0, `${selector} not found`);

    const rule = css.slice(start, css.indexOf("}", start));
    const literals = rule.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];

    assert.deepEqual(
      literals, [],
      `${selector} hardcodes ${literals.join(", ")} — use a token so both themes stay readable`
    );
  }
});

test("labels use an explicit colour rather than opacity", () => {
  // `opacity` multiplies against whatever is behind it, so one rule
  // produced ~4:1 contrast in dark mode and ~7:1 in light — a value
  // that cannot be verified because it depends on the backdrop.
  const styles = modalStyles();
  const label = styles.slice(styles.indexOf("\n      .label"));

  assert.ok(!label.slice(0, 120).includes("opacity"), ".label is back on opacity");
});

test("both themes define every token the rules read", () => {
  // A token used but never defined falls back to nothing, and the
  // property is simply dropped — an invisible panel rather than an
  // error.
  const styles = modalStyles();
  const cssStart = styles.indexOf("style.textContent = `");
  const css = styles.slice(cssStart, styles.indexOf("`;", cssStart));

  const used = new Set([...css.matchAll(/var\((--sg-[a-z-]+)\)/g)].map((m) => m[1]));
  const darkStart = css.indexOf("prefers-color-scheme: dark");

  const definedInLight = new Set(
    [...css.slice(0, darkStart).matchAll(/(--sg-[a-z-]+)\s*:/g)].map((m) => m[1])
  );
  const definedInDark = new Set(
    [...css.slice(darkStart).matchAll(/(--sg-[a-z-]+)\s*:/g)].map((m) => m[1])
  );

  for (const token of used) {
    assert.ok(definedInLight.has(token), `${token} has no light-theme value`);
    assert.ok(definedInDark.has(token), `${token} has no dark-theme value`);
  }
});
