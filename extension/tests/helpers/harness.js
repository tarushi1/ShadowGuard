/**
 * Test harness — a browser-shaped environment for the content scripts.
 * ================================================================
 *
 * The extension's files are classic scripts that attach to
 * `globalThis.SG` and are loaded by the manifest in a fixed order.
 * That design, chosen so content scripts could avoid ES modules, turns
 * out to make them straightforward to test: give them a `globalThis`
 * with a DOM on it and they load exactly as they do in Chrome.
 *
 * What this harness supplies, and why that matters
 * ------------------------------------------------
 * JSDOM does not implement everything the adapter uses. Missing here:
 * `execCommand`, `ClipboardEvent`, `DataTransfer`, `innerText` and
 * `requestAnimationFrame`. They are polyfilled below.
 *
 * BE HONEST ABOUT WHAT THAT MEANS. These tests verify OUR LOGIC —
 * the order of operations, the guard conditions, the verification
 * steps — against a model of how the browser behaves. They do not
 * verify Chrome's implementation, and they cannot: a polyfill that
 * matches our assumptions would pass even when our assumptions are
 * wrong.
 *
 * That is exactly the failure this project already hit. Four separate
 * fixes to the composer rewrite were verified against a mental model
 * of ProseMirror and all four were wrong (D39). So the fake editor in
 * `fake_editor.js` is built to reproduce the OBSERVED misbehaviour,
 * not the documented one — it appends on a collapsed selection and
 * reverts direct DOM writes, because that is what the real editor was
 * measured doing.
 *
 * Browser verification stays a manual step, listed in docs/testing.md.
 */

const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const EXTENSION_ROOT = path.resolve(__dirname, "..", "..");

/**
 * The load order from manifest.json, read from the manifest itself.
 *
 * Deliberately not hardcoded. Load order is load-bearing — `registry.js`
 * must run before any adapter calls `registerAdapter()` — and a test
 * suite that keeps its own copy of the order would keep passing after
 * someone reorders the manifest and breaks the extension.
 */
function manifestScripts() {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(EXTENSION_ROOT, "manifest.json"), "utf8")
  );

  return manifest.content_scripts[0].js;
}

/** Minimal `chrome` stand-in. Records calls so tests can assert on them. */
function fakeChrome(window, { manifestVersion = "0.0.0-test", onSendMessage } = {}) {
  const sent = [];

  return {
    sent,
    runtime: {
      getManifest: () => ({ version: manifestVersion }),
      sendMessage: async (message) => {
        sent.push(message);
        return onSendMessage ? onSendMessage(message) : { ok: true };
      },
      onMessage: { addListener() {} },
      lastError: null,
    },
    storage: {
      local: {
        get: async () => ({}),
        set: async () => {},
      },
    },
  };
}

/**
 * Fill the gaps JSDOM leaves.
 *
 * Each of these is a real API the adapter depends on. Where the
 * behaviour is subtle the polyfill errs towards being UNHELPFUL —
 * `execCommand` returns false unless a test opts in — so code under
 * test cannot pass by relying on a convenience the browser does not
 * actually provide.
 */
function polyfill(window) {
  // rAF, used by the composer rewrite to let the editor reconcile.
  window.requestAnimationFrame = (fn) => window.setTimeout(() => fn(Date.now()), 0);
  window.cancelAnimationFrame = (id) => window.clearTimeout(id);

  // innerText. JSDOM implements textContent only, and the difference
  // is a DETECTION-ACCURACY decision in this codebase: textContent
  // fuses adjacent block elements, so a key split across two lines
  // reads as one unbroken token. This approximation preserves that
  // distinction by joining block-level children with newlines.
  if (!("innerText" in window.HTMLElement.prototype)) {
    Object.defineProperty(window.HTMLElement.prototype, "innerText", {
      configurable: true,
      get() {
        const blocks = ["P", "DIV", "BR", "LI"];
        let out = "";

        const walk = (node) => {
          for (const child of node.childNodes) {
            if (child.nodeType === 3) {
              out += child.textContent;
            } else if (child.nodeType === 1) {
              if (child.tagName === "BR") {
                out += "\n";
                continue;
              }
              if (blocks.includes(child.tagName) && out && !out.endsWith("\n")) {
                out += "\n";
              }
              walk(child);
              if (blocks.includes(child.tagName) && !out.endsWith("\n")) out += "\n";
            }
          }
        };

        walk(this);
        return out.replace(/\n+$/, "");
      },
      set(value) {
        this.textContent = value;
      },
    });
  }

  // DataTransfer, used to carry text into a synthetic paste AND files
  // into an image replay. `items.add()` and the `files` list are both
  // load-bearing: setting `input.files` from a DataTransfer is the only
  // supported way to put a File into an input programmatically, and the
  // image interceptor's replay depends on it.
  if (!window.DataTransfer) {
    window.DataTransfer = class DataTransfer {
      #data = new Map();
      #files = [];

      constructor() {
        this.items = {
          add: (file) => {
            this.#files.push(file);
          },
        };
      }

      get files() {
        return this.#files;
      }

      setData(type, value) {
        this.#data.set(type, String(value));
      }

      getData(type) {
        return this.#data.get(type) ?? "";
      }
    };
  }

  // offsetParent. JSDOM performs no layout, so this is `undefined` on
  // every element — which means the adapters' visibility check
  // (`offsetParent !== null`) rejects everything, including buttons a
  // real browser would happily find.
  //
  // That check is not decoration: sites keep hidden fallback editors
  // and disabled send buttons in the DOM, and latching onto one means
  // reading a box the user never types in, or clicking a button that
  // silently does nothing.
  //
  // The approximation below is display-based only. It cannot model
  // visibility:hidden, zero dimensions, clipping, or an ancestor with
  // `display:none` set via a stylesheet rather than inline — all of
  // which occur in real pages. Another reason browser verification
  // stays a required step.
  // Defined unconditionally: JSDOM DOES declare offsetParent, it just
  // always answers null. A presence check therefore skips the polyfill
  // and leaves every element looking hidden — which cost two failing
  // tests and a confusing five minutes.
  {
    Object.defineProperty(window.HTMLElement.prototype, "offsetParent", {
      configurable: true,
      get() {
        for (let node = this; node; node = node.parentElement) {
          if (node.style?.display === "none" || node.hidden) return null;
        }

        return this.parentElement;
      },
    });
  }

  // `input.files` must be a real FileList in JSDOM, and a FileList
  // cannot be constructed from JavaScript — the only way to make one
  // in a browser is via DataTransfer, which JSDOM does not implement
  // either. So the property is relaxed to accept any array-like.
  //
  // This is a genuine gap between the harness and the browser, and it
  // is worth naming: in Chrome, `input.files = transfer.files` works
  // because DataTransfer.files IS a FileList. Here it works because
  // the check has been removed. A test that passes on this polyfill
  // therefore proves the interceptor's LOGIC, not that the assignment
  // is legal — which is one more reason browser verification stays a
  // required step.
  Object.defineProperty(window.HTMLInputElement.prototype, "files", {
    configurable: true,
    get() {
      return this.__files ?? [];
    },
    set(value) {
      this.__files = value ? [...value] : [];
    },
  });

  // A minimal File. JSDOM has one, but not always with a usable `type`
  // on a plain construction, and the interceptor filters on exactly
  // that.
  if (!window.File) {
    window.File = class File extends window.Blob {
      constructor(bits, name, options = {}) {
        super(bits, options);
        this.name = name;
        this.type = options.type ?? "";
      }
    };
  }

  // ClipboardEvent. The important part is that `clipboardData` survives
  // construction — that is what the editor reads.
  if (!window.ClipboardEvent) {
    window.ClipboardEvent = class ClipboardEvent extends window.Event {
      constructor(type, init = {}) {
        super(type, init);
        this.clipboardData = init.clipboardData ?? null;
      }
    };
  }

  // execCommand. Returns false by default: unimplemented in JSDOM, and
  // pretending otherwise would let code pass here that fails in Chrome.
  // A fake editor may replace this to model a specific behaviour.
  if (!window.document.execCommand) {
    window.document.execCommand = () => false;
  }

  // <dialog>. JSDOM parses the element but implements none of its
  // behaviour, so showModal(), close() and the `cancel` event are all
  // missing.
  //
  // This models the parts the modal depends on and NOT the browser's
  // top layer, which is the whole reason `<dialog>` was chosen over a
  // positioned div (D9). Top-layer rendering cannot be tested here at
  // all — it is a manual check, listed in docs/testing.md.
  const dialogProto = window.HTMLDialogElement?.prototype ?? window.HTMLElement.prototype;

  if (!dialogProto.showModal) {
    dialogProto.showModal = function showModal() {
      if (this.open) throw new window.DOMException("dialog already open", "InvalidStateError");
      this.open = true;
      this.setAttribute("open", "");
    };

    dialogProto.close = function close(returnValue) {
      if (!this.open) return;
      this.open = false;
      this.removeAttribute("open");
      if (returnValue !== undefined) this.returnValue = returnValue;
      this.dispatchEvent(new window.Event("close"));
    };
  }
}

/**
 * Build a window with the extension loaded into it.
 *
 * @param {object}  [options]
 * @param {string}  [options.html]      initial page markup
 * @param {string}  [options.url]       controls `location.hostname`
 * @param {string[]}[options.scripts]   override which files to load
 * @param {object}  [options.chrome]    options for the chrome stand-in
 * @returns {{window: Window, SG: object, chrome: object, console: object}}
 */
function load({
  html = "<!doctype html><html><body></body></html>",
  url = "https://chatgpt.com/",
  scripts = null,
  chrome: chromeOptions = {},
} = {}) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;

  polyfill(window);

  const chrome = fakeChrome(window, chromeOptions);
  window.chrome = chrome;

  // Captured rather than silenced. Several behaviours in this codebase
  // are DELIBERATELY reported through the console — a failed redaction,
  // a composer that changed after analysis — so tests need to assert on
  // it, and a test run should not be drowned in it either.
  const lines = [];
  const capture = (level) => (...args) => lines.push({ level, text: args.map(String).join(" ") });
  window.console = {
    log: capture("log"),
    warn: capture("warn"),
    error: capture("error"),
    debug: capture("debug"),
    info: capture("info"),
    group: capture("group"),
    groupCollapsed: capture("group"),
    groupEnd: () => {},
    table: capture("log"),
  };

  for (const relative of scripts ?? manifestScripts()) {
    const source = fs.readFileSync(path.join(EXTENSION_ROOT, relative), "utf8");
    window.eval(source);
  }

  return {
    window,
    SG: window.SG,
    chrome,
    console: {
      lines,
      /** Did anything logged at this level contain `needle`? */
      saw: (level, needle) =>
        lines.some((line) => line.level === level && line.text.includes(needle)),
      text: () => lines.map((l) => `${l.level}: ${l.text}`).join("\n"),
    },
  };
}

/** Everything except content.js, which self-starts and needs a live page. */
function loadLibraries(options = {}) {
  return load({
    ...options,
    scripts: manifestScripts().filter((file) => !file.endsWith("content/content.js")),
  });
}

/** Let queued timers and microtasks drain. */
function settle(window, frames = 3) {
  return new Promise((resolve) => {
    let remaining = frames;
    const tick = () => (remaining-- > 0 ? window.setTimeout(tick, 0) : resolve());
    tick();
  });
}

module.exports = { load, loadLibraries, manifestScripts, settle, EXTENSION_ROOT };
