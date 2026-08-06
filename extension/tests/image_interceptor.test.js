/**
 * Image interception — holding a screenshot before it leaves.
 * ================================================================
 *
 * The timing is what makes this component matter. Interception happens
 * at ATTACH, not at send: by send time ChatGPT has already uploaded
 * the file to its own servers and the composer holds only a remote
 * reference. **The bytes are gone.**
 *
 * So a bug here is not "the warning appeared late" — it is "the image
 * left the machine and we warned about it afterwards", which is the
 * observe-and-warn design D1 exists to reject.
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { loadLibraries } = require("./helpers/harness");

const PAGE = `<!doctype html><html><body>
  <form>
    <div id="prompt-textarea" contenteditable="true"></div>
    <input type="file" id="upload" accept="image/*">
  </form>
</body></html>`;

/**
 * @param {object} [options]
 * @param {boolean} [options.allow]  what the decision callback returns
 */
function setup({ allow = true } = {}) {
  const context = loadLibraries({ html: PAGE });
  const { window, SG } = context;

  const seen = [];
  const interceptor = new SG.ImageInterceptor(
    SG.resolveAdapter("chatgpt.com"),
    async (file, info) => {
      seen.push({ file, ...info });
      return allow;
    }
  );

  interceptor.start();

  return { ...context, interceptor, seen };
}

function image(window, name = "shot.png") {
  return new window.File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });
}

function pasteFiles(window, target, files) {
  const transfer = new window.DataTransfer();
  for (const file of files) transfer.items.add(file);

  const event = new window.ClipboardEvent("paste", {
    clipboardData: transfer,
    bubbles: true,
    cancelable: true,
  });

  target.dispatchEvent(event);
  return event;
}

// ---------------------------------------------------------------
// Paste
// ---------------------------------------------------------------

test("a pasted image is held and the page never sees it", async () => {
  const { window, seen } = setup();
  const composer = window.document.getElementById("prompt-textarea");

  const event = pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 1, "the image was not intercepted");
  assert.equal(seen[0].trigger, "paste");
  assert.equal(event.defaultPrevented, true, "the page will still receive this paste");
});

test("a text paste is left completely alone", async () => {
  // The listener sees every paste on the page. Cancelling an ordinary
  // text paste would break copy-and-paste for the whole site, which is
  // a faster route to an uninstall than any false positive.
  const { window, seen } = setup();
  const composer = window.document.getElementById("prompt-textarea");

  const transfer = new window.DataTransfer();
  transfer.setData("text/plain", "just some text");

  const event = new window.ClipboardEvent("paste", {
    clipboardData: transfer,
    bubbles: true,
    cancelable: true,
  });
  composer.dispatchEvent(event);

  assert.equal(seen.length, 0);
  assert.equal(event.defaultPrevented, false, "an ordinary paste was cancelled");
});

test("an approved image is re-pasted so the page can upload it", async () => {
  const { window, seen } = setup({ allow: true });
  const composer = window.document.getElementById("prompt-textarea");

  const replays = [];
  composer.addEventListener("paste", (event) => {
    if (event.defaultPrevented) return;
    replays.push([...(event.clipboardData?.files ?? [])]);
  });

  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 1);
  assert.equal(replays.length, 1, "an approved image was never re-pasted");
});

test("a rejected image is not re-pasted", async () => {
  const { window, seen } = setup({ allow: false });
  const composer = window.document.getElementById("prompt-textarea");

  const replays = [];
  composer.addEventListener("paste", (event) => {
    if (!event.defaultPrevented) replays.push(event);
  });

  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 1);
  assert.equal(replays.length, 0, "a rejected image reached the page anyway");
});

test("our own replay is not intercepted again", async () => {
  // The same problem `isTrusted` solves for keystrokes. Without the
  // approved-set the replay would be intercepted, analysed, approved,
  // replayed... forever.
  const { window, seen, interceptor } = setup({ allow: true });
  const composer = window.document.getElementById("prompt-textarea");

  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 1, `intercepted ${seen.length} times — replay loop`);
  assert.ok(interceptor.stats.passed >= 1, "the replay was not recognised as approved");
});

// ---------------------------------------------------------------
// File input
// ---------------------------------------------------------------

test("choosing a file is held, and the input is emptied", async () => {
  // `change` cannot be cancelled the way `paste` can — the browser has
  // already populated `input.files` by the time it fires. Emptying the
  // input is what actually prevents the upload.
  const { window, seen } = setup();
  const input = window.document.getElementById("upload");

  const transfer = new window.DataTransfer();
  transfer.items.add(image(window));
  input.files = transfer.files;

  input.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 1, "the file input path is open");
  assert.equal(seen[0].trigger, "file-input");
});

test("a non-image file input change is ignored", async () => {
  const { window, seen } = setup();
  const input = window.document.getElementById("upload");

  const transfer = new window.DataTransfer();
  transfer.items.add(new window.File(["x"], "notes.txt", { type: "text/plain" }));
  input.files = transfer.files;

  input.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(seen.length, 0, "a text file was sent for image analysis");
});

test("a change event on a non-file input is ignored", async () => {
  const { window, seen } = setup();
  const composer = window.document.getElementById("prompt-textarea");

  composer.dispatchEvent(new window.Event("change", { bubbles: true }));

  assert.equal(seen.length, 0);
});

// ---------------------------------------------------------------
// Control
// ---------------------------------------------------------------

test("disable() stops interception without unbinding", async () => {
  const { window, seen, interceptor } = setup();
  const composer = window.document.getElementById("prompt-textarea");

  interceptor.enabled = false;
  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));
  assert.equal(seen.length, 0);

  interceptor.enabled = true;
  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));
  assert.equal(seen.length, 1, "enable() did not restore interception");
});

test("stats count what actually happened", async () => {
  const { window, interceptor } = setup();
  const composer = window.document.getElementById("prompt-textarea");

  pasteFiles(window, composer, [image(window)]);
  await new Promise((r) => window.setTimeout(r, 0));

  assert.equal(interceptor.stats.intercepted, 1);
  assert.ok(interceptor.stats.seen >= 1);
});
