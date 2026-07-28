/**
 * ShadowGuard AI — Content Script
 * ================================================================
 * Runs INSIDE the page of a supported generative-AI platform.
 * This file is the orchestrator: it owns no site-specific knowledge
 * (that lives in src/adapters/) and no discovery logic (that lives
 * in composer_watcher.js). It wires the pieces together.
 *
 * What this file can do:
 *   - read and modify the page's DOM
 *   - render our warning modal over the page          (Week 2)
 *   - exchange JSON messages with the service worker
 *
 * What this file can NOT do:
 *   - see the page's own JavaScript variables (isolated world)
 *   - call our backend directly (that is the service worker's job)
 *
 * At this stage (Task 1.5) it locates the composer and reads what is
 * typed into it. Nothing is intercepted yet — that is Week 2.
 */

(() => {
  "use strict";

  const TAG = "[ShadowGuard]";

  // Single source of truth for the version: read it from the manifest
  // rather than hardcoding a constant in each file. Duplicated version
  // strings drift, and then a console log tells you that you are
  // running code you replaced twenty minutes ago.
  const VERSION = chrome.runtime.getManifest().version;

  /**
   * Guard against running twice in the same isolated world.
   *
   * `window` here is the ISOLATED world's window, not the page's, so
   * this property is invisible to ChatGPT's own code. A normal reload
   * gives us a fresh world, but any programmatic re-injection we add
   * later would reuse it — and two watchers would mean two warning
   * modals for one keystroke.
   */
  if (window.__shadowguardActive) {
    console.debug(`${TAG} already active in this frame — skipping re-injection.`);
    return;
  }
  window.__shadowguardActive = true;

  console.log(
    `%c ${TAG} ACTIVE `,
    "background:#0a7d47; color:#fff; padding:2px 6px; border-radius:3px; font-weight:bold;"
  );
  console.log(`${TAG} version    : ${VERSION}`);
  console.log(`${TAG} host       : ${location.hostname}`);
  console.log(`${TAG} frame      : ${window === window.top ? "top-level" : "iframe"}`);

  // ---------------------------------------------------------------
  // Adapter selection
  // ---------------------------------------------------------------
  // The manifest already restricts us to supported hosts, so a miss
  // here means the manifest and the adapter's `hosts` list disagree.
  // Bail loudly rather than run half-configured.
  const adapter = SG.resolveAdapter(location.hostname);

  if (!adapter) {
    console.error(
      `${TAG} no adapter registered for "${location.hostname}". ` +
      `Check that the manifest "matches" and the adapter "hosts" list agree.`
    );
    return;
  }
  console.log(`${TAG} adapter    : ${adapter.label} (${adapter.id})`);

  // ---------------------------------------------------------------
  // Composer discovery
  // ---------------------------------------------------------------
  /** @type {HTMLElement|null} the live composer, maintained by the watcher */
  let composer = null;

  /**
   * Debounce timer for the temporary text-reading instrumentation.
   * See attachInputLogger() for why this exists.
   */
  let readTimer = null;

  const watcher = new SG.ComposerWatcher(adapter, (element, info) => {
    composer = element;

    console.log(
      `${TAG} composer ${info.reattached ? "RE-attached (SPA navigation)" : "attached"}:`,
      element
    );

    attachInputLogger(element);
  });

  watcher.start();

  // ---------------------------------------------------------------
  // Temporary instrumentation — REMOVE BEFORE ANY REAL DEPLOYMENT
  // ---------------------------------------------------------------
  /**
   * Log what the user types, so Task 1.5 is visibly testable.
   *
   * PRIVACY NOTE, and it is not a small one: this prints the user's
   * raw prompt to the browser console. That is acceptable on your own
   * machine while building, and unacceptable in anything resembling
   * production — the console is readable by any other extension with
   * debugger access, and the whole point of this project is to stop
   * sensitive text from going where it should not. Week 2 replaces
   * this with a send-time read that never logs content.
   *
   * The debounce is not cosmetic. Without it this fires on every
   * keystroke; with it we act ~400ms after typing stops. That same
   * pattern carries into Week 3, where each call becomes an HTTP
   * request to the backend and un-debounced would mean one request
   * per character.
   *
   * @param {HTMLElement} element
   */
  function attachInputLogger(element) {
    // A re-attached element is a brand-new node, so it carries no
    // listeners from its predecessor and this cannot double-register.
    element.addEventListener("input", () => {
      clearTimeout(readTimer);

      readTimer = setTimeout(() => {
        if (adapter.isEmpty(element)) {
          console.log(`${TAG} composer is empty`);
          return;
        }

        const text = adapter.readText(element);
        console.log(`${TAG} read ${text.length} chars:`, JSON.stringify(text));
      }, 400);
    });
  }

  // ---------------------------------------------------------------
  // Manual debugging handle
  // ---------------------------------------------------------------
  // Lets you poke at the live state from the DevTools console.
  //
  // IMPORTANT: the console defaults to the PAGE's world, where this
  // does not exist. Use the context dropdown (top-left of the Console
  // panel, usually reading "top") and switch to "ShadowGuard AI".
  // Forgetting this step produces "__sg is not defined" on perfectly
  // working code, and costs beginners an hour.
  window.__sg = {
    adapter,
    watcher,
    get composer() {
      return composer;
    },
    read: () => adapter.readText(composer),
    isEmpty: () => adapter.isEmpty(composer),
  };

  // ---------------------------------------------------------------
  // Bridge check (Task 1.4)
  // ---------------------------------------------------------------
  // In Week 3 this same call carries the prompt and the reply carries
  // the risk verdict — only the payload changes.
  chrome.runtime
    .sendMessage({
      type: SG.MSG.PING,
      payload: { host: location.hostname, sentAt: new Date().toISOString() },
    })
    .then((response) => {
      console.log(`${TAG} bridge OK — reply from service worker:`, response);
    })
    .catch((error) => {
      // Usually the service worker failed to load at all (a syntax
      // error in service_worker.js), leaving nothing listening.
      console.error(`${TAG} bridge FAILED:`, error.message);
    });
})();
