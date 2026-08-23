/**
 * PDF attachments, through content.js.
 * ================================================================
 *
 * These exercise the ORCHESTRATION, not the interceptor: which
 * endpoint a file is routed to, and which dialog its verdict produces.
 * The interceptor's own tests cover holding and replaying.
 *
 * The first test is a regression, and worth reading before the others
 * because the bug it pins is instructive. The "this PDF could not be
 * read" notice was written to stop a scan being waved through in
 * silence — a good fix. It was placed BEFORE the check on the verdict,
 * so a document with no page text but a payment card in its form
 * fields — blocked, score 100 — was shown "we could not read this"
 * with a Proceed button under fail-open policy.
 *
 * A safety notice that converts a BLOCK into an override prompt is a
 * worse bug than the silence it replaced. It was found by running real
 * documents through the whole stack and reading the table, not by any
 * test that existed at the time.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { load, manifestScripts, settle } = require("./helpers/harness");

const PAGE = `<!doctype html><html><body>
  <form>
    <div id="prompt-textarea" contenteditable="true">here is the file</div>
    <button data-testid="send-button">send</button>
  </form>
</body></html>`;

/** Boot with a scripted worker, recording which message types it saw. */
function boot(verdict) {
  const asked = [];

  const context = load({
    html: PAGE,
    scripts: manifestScripts(),
    chrome: {
      onSendMessage: async (message) => {
        asked.push(message.type);
        return { ok: true, verdict, roundTripMs: 1 };
      },
    },
  });

  context.window.__sg.interceptor.isTrusted = (event) => event.__user === true;
  return { context, asked };
}

function pdfFile(window, name = "claim.pdf") {
  return new window.File([new Uint8Array([37, 80, 68, 70])], name, {
    type: "application/pdf",
  });
}

function imageFile(window) {
  return new window.File([new Uint8Array([137, 80, 78, 71])], "shot.png", {
    type: "image/png",
  });
}

function paste(window, file) {
  const transfer = new window.DataTransfer();
  transfer.items.add(file);

  window.document.getElementById("prompt-textarea").dispatchEvent(
    new window.ClipboardEvent("paste", {
      clipboardData: transfer,
      bubbles: true,
      cancelable: true,
    })
  );
}

/** What the dialog currently says. */
function dialog(context) {
  const modal = context.window.__sg.modal;
  if (!modal.dialog) return null;

  return {
    open: modal.isOpen,
    title: modal.dialog.querySelector("#sg-title").textContent,
    message: modal.dialog.querySelector("#sg-msg").textContent,
    preview: modal.dialog.querySelector("#sg-preview").textContent,
    canProceed: modal.dialog.querySelector("#sg-proceed").hidden === false,
  };
}

const BLOCKED_FORM = {
  action: "block",
  level: "critical",
  score: 100,
  entities: [{ type: "CREDIT_CARD" }],
  message: "This document contains a payment card number.",
  latency_ms: 4,
  request_id: "t1",
  // No page text — the card was in a form field. This combination is
  // the whole point of the test.
  pdf: {
    characters: 60, pages: 1, pages_read: 1,
    has_text_layer: false, truncated: false, encrypted: false,
    sources: ["forms"],
  },
};

const UNREADABLE_SCAN = {
  action: "allow",
  level: "low",
  score: 0,
  entities: [],
  message:
    "This PDF has no text layer — it is most likely a scan or a photograph. " +
    "ShadowGuard could not read it, so it has NOT been checked for sensitive content.",
  latency_ms: 3,
  request_id: "t2",
  pdf: {
    characters: 0, pages: 3, pages_read: 3,
    has_text_layer: false, truncated: false, encrypted: false,
    sources: [],
  },
};

// ---------------------------------------------------------------
// The regression
// ---------------------------------------------------------------

test("a blocked document is blocked, even with no text layer", async () => {
  const { context } = boot(BLOCKED_FORM);

  paste(context.window, pdfFile(context.window));
  await settle(context.window, 8);

  const shown = dialog(context);

  assert.ok(shown?.open, "no dialog was shown for a blocked document");
  assert.equal(
    shown.canProceed, false,
    "a BLOCKED document offered a Proceed button — the unread notice " +
    "overrode the verdict"
  );
  assert.match(
    shown.message, /payment card/,
    "the dialog described the document as unreadable instead of as blocked"
  );
});

test("a partly-read document still says its pages were not read", async () => {
  // The information must not be lost by fixing the bug above. The
  // verdict takes priority, and the preview line still carries the
  // fact that no page text was available.
  const { context } = boot(BLOCKED_FORM);

  paste(context.window, pdfFile(context.window));
  await settle(context.window, 8);

  assert.match(dialog(context).preview, /NO TEXT LAYER/);
});

// ---------------------------------------------------------------
// The case the notice was written for
// ---------------------------------------------------------------

test("an unreadable scan is reported as unread, not as clean", async () => {
  // Without this, "print it, scan it, attach it" defeats every rule in
  // the system in one step, and does so silently.
  const { context } = boot(UNREADABLE_SCAN);

  paste(context.window, pdfFile(context.window));
  await settle(context.window, 8);

  const shown = dialog(context);

  assert.ok(shown?.open, "a scan was waved through with no notice at all");
  assert.match(shown.message, /NOT been checked/);
  assert.doesNotMatch(
    shown.message, /no sensitive content (was )?found/i,
    "an unread document was described as clean"
  );
});

// ---------------------------------------------------------------
// Routing
// ---------------------------------------------------------------

test("a PDF goes to the PDF endpoint and an image to the image one", async () => {
  const clean = {
    action: "allow", level: "low", score: 0, entities: [],
    message: "ok", latency_ms: 1, request_id: "t3",
    pdf: {
      characters: 40, pages: 1, pages_read: 1,
      has_text_layer: true, truncated: false, encrypted: false, sources: ["pages"],
    },
  };

  const pdfRun = boot(clean);
  paste(pdfRun.context.window, pdfFile(pdfRun.context.window));
  await settle(pdfRun.context.window, 8);

  assert.ok(
    pdfRun.asked.includes("SG_ANALYZE_PDF"),
    `a PDF was not routed to the PDF endpoint: ${JSON.stringify(pdfRun.asked)}`
  );

  const imageRun = boot({
    action: "allow", level: "low", score: 0, entities: [],
    message: "ok", latency_ms: 1, request_id: "t4",
    ocr: { characters: 0, words: 0, confidence: 0.9, width: 10, height: 10 },
  });
  paste(imageRun.context.window, imageFile(imageRun.context.window));
  await settle(imageRun.context.window, 8);

  assert.ok(
    imageRun.asked.includes("SG_ANALYZE_IMAGE"),
    `an image was not routed to the image endpoint: ${JSON.stringify(imageRun.asked)}`
  );
  assert.ok(
    !imageRun.asked.includes("SG_ANALYZE_PDF"),
    "an image was sent to the PDF endpoint"
  );
});

test("a clean, readable PDF passes with no dialog at all", async () => {
  const { context } = boot({
    action: "allow", level: "low", score: 0, entities: [],
    message: "No sensitive content was found in this document.",
    latency_ms: 1, request_id: "t5",
    pdf: {
      characters: 400, pages: 2, pages_read: 2,
      has_text_layer: true, truncated: false, encrypted: false, sources: ["pages"],
    },
  });

  paste(context.window, pdfFile(context.window));
  await settle(context.window, 8);

  assert.equal(
    context.window.__sg.modal.isOpen, false,
    "a clean document interrupted the user"
  );
});
