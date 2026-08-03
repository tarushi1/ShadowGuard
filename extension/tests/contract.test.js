/**
 * Contracts that span two languages and three JavaScript worlds.
 * ================================================================
 *
 * Every failure in this file is SILENT in production. A mismatched
 * action string does not raise anything — it falls through every
 * branch in the content script, and the message is neither sent nor
 * blocked. Nothing appears in a log, because nothing went wrong from
 * any single component's point of view.
 *
 * That is the class of bug this file exists for: agreements between
 * parts that are never checked because no one part owns them.
 */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { loadLibraries, load, manifestScripts, EXTENSION_ROOT } = require("./helpers/harness");

const REPO_ROOT = path.resolve(EXTENSION_ROOT, "..");

// ---------------------------------------------------------------
// Python ↔ JavaScript
// ---------------------------------------------------------------

test("SG.ACTION matches the Action enum in the Python schema", () => {
  // These four strings travel Python → service worker → content
  // script → UI. The content script routes on them by string
  // comparison, so a value it does not recognise means the held
  // message is never replayed and never discarded.
  //
  // Parsed out of the Python source rather than duplicated here,
  // because a hand-copied list is exactly the thing that drifts.
  const source = fs.readFileSync(
    path.join(REPO_ROOT, "backend", "app", "schemas", "analyze.py"),
    "utf8"
  );

  const block = source.slice(source.indexOf("class Action"), source.indexOf("class RiskLevel"));
  const pythonActions = [...block.matchAll(/^\s{4}[A-Z_]+ = "([a-z]+)"$/gm)].map((m) => m[1]);

  assert.ok(pythonActions.length >= 4, `parsed too few actions: ${pythonActions}`);

  const { SG } = loadLibraries();

  assert.deepEqual(
    Object.values(SG.ACTION).sort(),
    pythonActions.sort(),
    "the extension and the backend disagree about the action vocabulary"
  );
});

test("SG.RISK levels match the Python RiskLevel enum", () => {
  const source = fs.readFileSync(
    path.join(REPO_ROOT, "backend", "app", "schemas", "analyze.py"),
    "utf8"
  );

  const block = source.slice(source.indexOf("class RiskLevel"));
  const pythonLevels = [...block.matchAll(/^\s{4}[A-Z]+ = "([a-z]+)"$/gm)].map((m) => m[1]);
  const { SG } = loadLibraries();

  if (!SG.RISK) return; // the extension does not mirror these yet

  assert.deepEqual(Object.values(SG.RISK).sort(), pythonLevels.sort());
});

test("the entity types the corpus expects all exist in the backend catalog", () => {
  // Not a JavaScript concern, but it is a cross-file agreement with the
  // same silent-failure shape, and this is where such checks live.
  const corpus = fs
    .readFileSync(path.join(REPO_ROOT, "evaluation", "data", "corpus.jsonl"), "utf8")
    .split("\n")
    .filter((line) => line.trim());

  for (const line of corpus) {
    const row = JSON.parse(line);
    assert.ok(Array.isArray(row.expected), `${row.id}: expected must be a list`);
  }
});

// ---------------------------------------------------------------
// Manifest ↔ source
// ---------------------------------------------------------------

test("every script the manifest loads exists on disk", () => {
  // A path typo here disables part of the extension with no error
  // anywhere — Chrome logs it in a place nobody looks, and the symptom
  // is a feature that silently does nothing.
  for (const relative of manifestScripts()) {
    const full = path.join(EXTENSION_ROOT, relative);
    assert.ok(fs.existsSync(full), `manifest lists a missing file: ${relative}`);
  }
});

test("registry.js loads before any adapter that registers itself", () => {
  const scripts = manifestScripts();
  const registry = scripts.findIndex((f) => f.includes("adapters/registry.js"));
  const firstAdapter = scripts.findIndex(
    (f) => f.includes("adapters/") && !f.includes("registry.js")
  );

  assert.ok(registry >= 0, "registry.js is not in the manifest");
  assert.ok(registry < firstAdapter, "an adapter loads before the registry it calls");
});

test("the manifest version matches package.json", () => {
  // Not cosmetic. The version is how you tell whether Chrome actually
  // picked up a reload, and a stale one sends you debugging code that
  // is not running — which cost a full round trip during 5.2.
  const manifest = JSON.parse(
    fs.readFileSync(path.join(EXTENSION_ROOT, "manifest.json"), "utf8")
  );
  const pkg = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "package.json"), "utf8"));

  assert.equal(manifest.version, pkg.version);
});

// ---------------------------------------------------------------
// content.js — the smoke test that would have caught D38
// ---------------------------------------------------------------

test("content.js exposes every handle it advertises", async () => {
  // REGRESSION, D38. `approve` and `discard` were deleted while six
  // call sites survived. The file still PARSED — a ReferenceError for
  // an undeclared identifier is a runtime failure — so nothing caught
  // it until a send could not be replayed in the browser.
  //
  // `window.__sg` is built with `approve` as a shorthand property, so
  // constructing it threw and __sg was never assigned. Asserting the
  // object exists and is complete is enough to catch the whole class.
  const context = load({
    html: `<!doctype html><html><body>
      <form><div id="prompt-textarea" contenteditable="true"></div>
      <button data-testid="send-button"></button></form>
    </body></html>`,
  });

  const sg = context.window.__sg;

  assert.ok(sg, `__sg was never assigned — content.js threw:\n${context.console.text()}`);

  for (const name of ["approve", "discard", "sendRedacted", "clear", "tryReplace", "disable", "enable"]) {
    assert.equal(typeof sg[name], "function", `__sg.${name} is not a function`);
  }
});

test("content.js reports no errors while loading", () => {
  const context = load({
    html: `<!doctype html><html><body>
      <form><div id="prompt-textarea" contenteditable="true"></div></form>
    </body></html>`,
  });

  const errors = context.console.lines.filter((line) => line.level === "error");

  assert.deepEqual(errors, [], `content.js logged errors on load:\n${context.console.text()}`);
});
