/**
 * ShadowGuard AI — Service Worker (background)
 * ================================================================
 * World 3. One instance shared by every tab.
 *
 * Responsibilities:
 *   - receive analysis requests from content scripts
 *   - call the ShadowGuard backend over HTTP  (Week 3)
 *   - return the verdict to the requesting tab
 *
 * Hard constraints to keep in mind while editing this file:
 *   - NO DOM. `document` and `window` do not exist here.
 *   - EPHEMERAL. Chrome terminates this worker after ~30s idle and
 *     every global is lost. Never store state that must survive in
 *     a module-level variable — use chrome.storage instead.
 */

"use strict";

// Classic (non-module) service workers load dependencies this way.
// The leading slash resolves from the extension root, so this path
// stays correct regardless of where this file sits.
importScripts("/src/shared/messages.js");

const TAG = "[ShadowGuard:SW]";

console.log(`${TAG} service worker script evaluated at ${new Date().toISOString()}`);

// ---------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------
// onInstalled fires once on install and again on every update or
// manual reload — NOT on each wake-up. Useful later for seeding
// default settings; useful now to confirm registration worked.
chrome.runtime.onInstalled.addListener((details) => {
  console.log(`${TAG} onInstalled — reason: ${details.reason}`);
});

// ---------------------------------------------------------------
// Message router
// ---------------------------------------------------------------
/**
 * Handles every message sent from any content script.
 *
 * @param {object} message  JSON-serializable payload; we require a `type` field.
 * @param {object} sender   Chrome-supplied provenance (tab, url, frameId).
 * @param {function} sendResponse  Callback that returns a value to the sender.
 * @returns {boolean} true to keep the response channel open for async work.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // `sender` is populated by Chrome itself, not by the content script,
  // so it cannot be spoofed by a malicious page. That makes it the
  // trustworthy source for which tab and URL a request came from —
  // which matters once we start attributing events to users.
  const origin = sender.tab ? `tab ${sender.tab.id} (${sender.tab.url})` : "unknown sender";
  console.log(`${TAG} received:`, message, `from ${origin}`);

  switch (message?.type) {
    case SG.MSG.PING: {
      sendResponse({
        type: SG.MSG.PONG,
        receivedAt: new Date().toISOString(),
        echo: message.payload ?? null,
        workerVersion: "0.1.0",
      });
      break;
    }

    default: {
      // Unknown types are logged rather than ignored. Silent drops are
      // the hardest messaging bug to diagnose, because the sender just
      // waits and nothing anywhere reports a problem.
      console.warn(`${TAG} unhandled message type: ${message?.type}`);
      sendResponse({ error: `unhandled message type: ${message?.type}` });
      break;
    }
  }

  // ---- The gotcha that breaks most first extensions -------------
  // Returning true tells Chrome "I may call sendResponse later, keep
  // the channel open". Without it, Chrome closes the channel the
  // moment this function returns, and any async sendResponse throws
  // "The message port closed before a response was received."
  //
  // Our handlers above are synchronous and would work without this,
  // but from Week 3 they will await a fetch() to the backend. Getting
  // it right now means the bug never appears.
  return true;
});
