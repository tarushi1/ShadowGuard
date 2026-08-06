/**
 * The adapter layer — the claim D2 made, tested.
 * ================================================================
 *
 * Week 1 chose a pluggable adapter registry over `if (host === ...)`
 * branching, on the claim that a second platform would be one new file
 * that CANNOT break the first.
 *
 * That is a testable claim, and this file tests it. It is also a claim
 * that was briefly false: the first version of `gemini.js` omitted
 * three required methods, `registerAdapter` threw, and the throw took
 * down the whole content script — for both platforms. Tests 1 and 2
 * below are what caught it.
 */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { loadLibraries, manifestScripts, EXTENSION_ROOT } = require("./helpers/harness");

// ---------------------------------------------------------------
// Registration
// ---------------------------------------------------------------

test("every adapter registers without throwing", () => {
  // `registerAdapter` throws on a missing method, and the throw
  // escapes the adapter's IIFE — so one malformed adapter file breaks
  // the load for every platform, not just its own.
  const context = loadLibraries();

  assert.deepEqual(
    context.console.lines.filter((line) => line.level === "error"),
    [],
    `an adapter failed to register:\n${context.console.text()}`
  );
});

test("both platforms resolve to their own adapter", () => {
  const { SG } = loadLibraries();

  assert.equal(SG.resolveAdapter("chatgpt.com")?.id, "chatgpt");
  assert.equal(SG.resolveAdapter("chat.openai.com")?.id, "chatgpt");
  assert.equal(SG.resolveAdapter("gemini.google.com")?.id, "gemini");
  assert.equal(SG.resolveAdapter("example.com"), null);
});

test("every adapter satisfies the full interface", () => {
  // An adapter missing a method does not fail at registration if the
  // defaults supplied it — but it must still END UP complete, because
  // the interceptor calls all of these without checking.
  const { SG } = loadLibraries();

  const required = [
    "id", "label", "hosts", "selectors",
    "readText", "isEmpty", "matchSendButton", "findSendButton", "send", "replaceText",
  ];

  for (const adapter of SG.adapters) {
    for (const key of required) {
      assert.notEqual(adapter[key], undefined, `${adapter.id} is missing ${key}`);
    }
  }
});

test("adding a platform cannot break an existing one", () => {
  // The whole point of D2, asserted directly: load with Gemini present
  // and confirm ChatGPT still resolves and still works.
  const { SG, window } = loadLibraries({
    html: `<!doctype html><html><body>
      <button data-testid="send-button"><svg><path id="g"/></svg></button>
    </body></html>`,
  });

  const chatgpt = SG.resolveAdapter("chatgpt.com");

  assert.ok(chatgpt.matchSendButton(window.document.getElementById("g")));
  assert.equal(chatgpt.findSendButton()?.tier, 1);
});

// ---------------------------------------------------------------
// Manifest agreement
// ---------------------------------------------------------------

test("every host an adapter claims is one the manifest injects into", () => {
  // A host in the adapter but not the manifest means the content
  // script never runs there and the platform is silently unsupported.
  // The reverse means the script runs and finds no adapter, then bails
  // with an error nobody sees.
  const manifest = JSON.parse(
    fs.readFileSync(path.join(EXTENSION_ROOT, "manifest.json"), "utf8")
  );
  const patterns = manifest.content_scripts[0].matches.join(" ");
  const { SG } = loadLibraries();

  for (const adapter of SG.adapters) {
    for (const host of adapter.hosts) {
      assert.ok(
        patterns.includes(host),
        `${adapter.id} claims ${host}, which the manifest does not match`
      );
    }
  }
});

test("every adapter file the manifest loads is listed before the content script", () => {
  const scripts = manifestScripts();
  const lastAdapter = scripts.map((f) => f.includes("adapters/")).lastIndexOf(true);
  const contentScript = scripts.findIndex((f) => f.endsWith("content/content.js"));

  assert.ok(lastAdapter < contentScript, "an adapter loads after the orchestrator");
});

// ---------------------------------------------------------------
// The generic defaults
// ---------------------------------------------------------------

test("an adapter declaring only selectors gets a working implementation", () => {
  // The property that makes "adding a platform is one file" true. If
  // this fails, every new adapter has to copy ChatGPT's methods, and
  // the copies drift.
  const { SG, window } = loadLibraries({
    html: `<!doctype html><html><body>
      <div id="c" contenteditable="true">hello</div>
      <button class="fake-send"><span id="inner">go</span></button>
    </body></html>`,
  });

  SG.registerAdapter({
    id: "test-platform",
    label: "Test",
    hosts: ["test.example"],
    selectors: { composer: ["#c"], sendButton: [".fake-send"] },
    readText: (el) => el?.innerText ?? "",
    isEmpty(el) {
      return this.readText(el).trim().length === 0;
    },
  });

  const adapter = SG.resolveAdapter("test.example");

  assert.ok(adapter.matchSendButton(window.document.getElementById("inner")));
  assert.equal(adapter.findSendButton()?.selector, ".fake-send");
  assert.equal(typeof adapter.send, "function");
});

test("the default findSendButton skips disabled and hidden buttons", () => {
  // Replaying into a disabled button silently no-ops: the user is told
  // their message was sent and it was not.
  const { SG } = loadLibraries({
    html: `<!doctype html><html><body>
      <button class="s" disabled></button>
    </body></html>`,
  });

  SG.registerAdapter({
    id: "disabled-test",
    label: "T",
    hosts: ["d.example"],
    selectors: { composer: ["#nothing"], sendButton: [".s"] },
    readText: () => "",
    isEmpty: () => true,
  });

  assert.equal(SG.resolveAdapter("d.example").findSendButton(), null);
});

// ---------------------------------------------------------------
// Gemini specifics
// ---------------------------------------------------------------

test("gemini's composer selectors are tiered most-specific first", () => {
  // The tier that matches is the health signal. A bare
  // `div[contenteditable]` first would match the wrong element on a
  // rich page and report tier 1 while being completely wrong.
  const { SG } = loadLibraries();
  const tiers = SG.resolveAdapter("gemini.google.com").selectors.composer;

  assert.ok(tiers.length >= 3, "no fallback tiers — one DOM change disables the adapter");
  assert.equal(
    tiers[tiers.length - 1],
    "div[contenteditable='true']",
    "the last resort should be the least specific selector"
  );
  assert.ok(
    tiers[0].length > tiers[tiers.length - 1].length,
    "the first tier is not more specific than the last"
  );
});

test("gemini reads innerText, so a key split across lines stays split", () => {
  // Quill renders each line as its own block. textContent fuses them,
  // and a key broken across two lines would read as one unbroken token
  // — a detection-accuracy bug, not a formatting one.
  const { SG, window } = loadLibraries({
    html: `<!doctype html><html><body>
      <div id="c" contenteditable="true"><p>sk-proj-abc</p><p>def123</p></div>
    </body></html>`,
  });

  const composer = window.document.getElementById("c");

  assert.equal(composer.textContent, "sk-proj-abcdef123", "precondition");
  assert.ok(SG.resolveAdapter("gemini.google.com").readText(composer).includes("\n"));
});

test("gemini treats Quill's placeholder paragraph as empty", () => {
  const { SG, window } = loadLibraries({
    html: `<!doctype html><html><body>
      <div id="c" contenteditable="true"><p><br></p></div>
    </body></html>`,
  });

  assert.equal(
    SG.resolveAdapter("gemini.google.com").isEmpty(window.document.getElementById("c")),
    true
  );
});
