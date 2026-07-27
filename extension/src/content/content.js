/**
 * ShadowGuard AI — Content Script
 * ================================================================
 * Runs INSIDE the page of a supported generative-AI platform.
 *
 * What this file can do:
 *   - read and modify the page's DOM (find the composer, attach listeners)
 *   - render our warning modal over the page
 *   - exchange JSON messages with the service worker
 *
 * What this file can NOT do:
 *   - see the page's own JavaScript variables (isolated world)
 *   - call our backend directly (cross-origin requests are the
 *     service worker's job; see src/background/)
 *
 * At this stage (Task 1.3) it only proves it is alive and can reach
 * the DOM. Interception logic arrives in Week 2.
 */

(() => {
  "use strict";

  const VERSION = "0.1.0";
  const TAG = "[ShadowGuard]";

  /**
   * Guard against running twice in the same isolated world.
   *
   * `window` here is the ISOLATED world's window object, not the page's,
   * so this property is invisible to ChatGPT's own code and cannot clash
   * with it. A normal page reload gives us a fresh world (so this flag
   * resets), but any programmatic re-injection we add later would reuse
   * the same world — and duplicate keydown listeners would mean the user
   * gets our warning modal twice for one keystroke.
   */
  if (window.__shadowguardActive) {
    console.debug(`${TAG} already active in this frame — skipping re-injection.`);
    return;
  }
  window.__shadowguardActive = true;

  // ---------------------------------------------------------------
  // Activation banner
  // ---------------------------------------------------------------
  // Styled so it is instantly findable in a console that ChatGPT
  // itself floods with output. %c applies CSS to the following text.
  console.log(
    `%c ${TAG} ACTIVE `,
    "background:#0a7d47; color:#fff; padding:2px 6px; border-radius:3px; font-weight:bold;"
  );

  console.log(`${TAG} version    : ${VERSION}`);
  console.log(`${TAG} host       : ${location.hostname}`);
  console.log(`${TAG} readyState : ${document.readyState}`);
  console.log(`${TAG} frame      : ${window === window.top ? "top-level" : "iframe"}`);

  // ---------------------------------------------------------------
  // Proof of DOM access
  // ---------------------------------------------------------------
  // A preview of Task 1.5. ChatGPT's message box is a contenteditable
  // <div>, not a <textarea> — but we query both, because relying on a
  // single assumption about another team's markup is how extensions
  // silently break. The count printed here tells us what we are really
  // dealing with; we build the actual selector in Task 1.5.
  const candidates = document.querySelectorAll(
    "textarea, [contenteditable='true']"
  );
  console.log(`${TAG} candidate input elements: ${candidates.length}`);

  // If the SPA has not rendered the composer yet, this will be 0 — which
  // is not a failure, it is the exact problem MutationObserver solves.
  if (candidates.length === 0) {
    console.warn(
      `${TAG} no input elements yet — the SPA has probably not rendered ` +
      `the composer. Expected at this stage; handled in Week 2.`
    );
  }

  // ---------------------------------------------------------------
  // Bridge check (Task 1.4)
  // ---------------------------------------------------------------
  // Prove the content script can reach the service worker and get a
  // reply. In Week 3 this same call carries the user's prompt and the
  // reply carries the risk verdict — only the payload changes.
  //
  // sendMessage returns a Promise in MV3, so we can await it. The
  // older callback form still works but nests awkwardly once real
  // async work is involved.
  chrome.runtime
    .sendMessage({
      type: SG.MSG.PING,
      payload: { host: location.hostname, sentAt: new Date().toISOString() },
    })
    .then((response) => {
      console.log(`${TAG} bridge OK — reply from service worker:`, response);
    })
    .catch((error) => {
      // The usual cause is the service worker failing to load at all
      // (a syntax error in service_worker.js), which leaves nothing
      // listening on the other end.
      console.error(`${TAG} bridge FAILED:`, error.message);
    });
})();
