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
 * At this stage (Task 2.3) it cancels every send path — Enter, the
 * send button, form submission — holds the message, and can replay it
 * on approval. The approval still comes from the console; Tasks 2.4
 * and 2.5 replace that with the modal, and Week 3 makes the verdict
 * come from the backend rather than a human guess.
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
   * How long to wait before checking that a replayed send actually
   * took effect. Long enough for React to re-render and clear the
   * composer, short enough that a failure is reported while the user
   * is still looking at the screen.
   */
  const VERIFY_DELAY_MS = 500;

  /**
   * The send we cancelled and are holding, or null.
   * Declared here so the interceptor callback and the approve/discard
   * helpers below all close over the same binding.
   * @type {{text: string, trigger: string, at: number}|null}
   */
  let pending = null;

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

  const watcher = new SG.ComposerWatcher(adapter, (element, info) => {
    composer = element;
    console.log(
      `${TAG} composer ${info.reattached ? "RE-attached (SPA navigation)" : "attached"}:`,
      element
    );
  });

  watcher.start();

  // ---------------------------------------------------------------
  // Send interception
  // ---------------------------------------------------------------
  // The interceptor asks for the composer through a getter rather
  // than receiving the element once. The watcher swaps `composer` out
  // on every SPA navigation, and a captured reference would go stale
  // silently — the listener would keep firing and keep comparing the
  // event target against a node that left the document minutes ago.
  const interceptor = new SG.Interceptor(
    adapter,
    () => composer,
    (text, context) => {
      // Hold the cancelled send. In Task 2.5 the modal reads this and
      // the user's button click resolves it; today it waits for a
      // manual __sg.approve() or __sg.discard() from the console.
      //
      // We store the text even though the composer still contains it,
      // because from Week 3 this is the exact string that was sent to
      // the backend and scored. If the user edits the box while the
      // modal is open, approving must not send text that was never
      // analysed — a check we can only make by comparing against what
      // we captured. That is a real bypass, not a hypothetical one.
      pending = { text, trigger: context.trigger, at: Date.now() };

      console.group(`%c ${TAG} SEND BLOCKED `, "background:#b3261e; color:#fff; font-weight:bold;");
      console.log("trigger :", context.trigger);
      console.log("length  :", text.length, "chars");

      // TEMPORARY — REMOVE BEFORE ANY REAL USE.
      // Printing the raw prompt to the console is exactly the class
      // of data exposure this project exists to prevent. It is here
      // only so this task is visibly testable on your own machine.
      // From Week 3 the text goes to the backend and is never logged.
      console.log("text    :", JSON.stringify(text));

      console.log(
        "%crun __sg.approve() to send, or __sg.discard() to keep it blocked.",
        "color:#888; font-style:italic;"
      );
      console.groupEnd();
    }
  );

  interceptor.start();

  // ---------------------------------------------------------------
  // Pending send + replay
  // ---------------------------------------------------------------
  /**
   * Send the held message.
   *
   * Stands in for the modal's "Proceed" button until Task 2.5.
   *
   * @returns {boolean} whether a replay was attempted
   */
  function approve() {
    if (!pending) {
      console.warn(`${TAG} nothing pending to approve.`);
      return false;
    }
    if (!composer) {
      console.error(`${TAG} composer went away — cannot replay.`);
      pending = null;
      return false;
    }

    // The composer is editable while a decision is outstanding, so
    // the user can change the text after it was analysed and before
    // they approve. Sending the edited version would mean shipping
    // text that never passed inspection. Warn now; Week 5 turns this
    // into a re-analysis.
    const current = adapter.readText(composer);
    if (current !== pending.text) {
      console.warn(
        `${TAG} composer text changed since it was inspected ` +
        `(${pending.text.length} → ${current.length} chars). ` +
        `Re-analysis on edit is Week 5 work.`
      );
    }

    const result = adapter.send(composer);
    const held = pending;
    pending = null;

    console.log(
      `%c ${TAG} SEND REPLAYED `,
      "background:#0a7d47; color:#fff; padding:2px 6px; border-radius:3px;",
      `via ${result.method} (${result.detail})`
    );

    // ---- Verify the effect, do not assume the action worked ----
    // A send that silently no-ops is the worst outcome: the user
    // believes their approved message went, and it did not.
    //
    // Deliberately a warning and NOT an auto-retry. Retrying a failed
    // action is good practice in most apps; in a DLP tool it means
    // the message you were already unsure about gets sent twice. A
    // loud warning beats a silent double-send.
    setTimeout(() => {
      if (composer && !adapter.isEmpty(composer)) {
        console.warn(
          `${TAG} composer still has text ${VERIFY_DELAY_MS}ms after replay — ` +
          `the send may not have gone through. Not retrying automatically.`
        );
      }
    }, VERIFY_DELAY_MS);

    return held !== null;
  }

  /** Drop the held message. The text stays in the box so it can be edited. */
  function discard() {
    if (!pending) {
      console.warn(`${TAG} nothing pending to discard.`);
      return false;
    }
    pending = null;
    console.log(`${TAG} pending send discarded — text left in the composer for editing.`);
    return true;
  }

  console.log(
    `%c ${TAG} INTERCEPTING `,
    "background:#8a4b00; color:#fff; padding:2px 6px; border-radius:3px;",
    "keydown · click · submit — all send paths closed"
  );
  console.log(`${TAG} run __sg.disable() to use ChatGPT normally while developing.`);

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
    interceptor,
    get composer() {
      return composer;
    },
    read: () => adapter.readText(composer),
    isEmpty: () => adapter.isEmpty(composer),

    /** Stop blocking Enter, so you can use ChatGPT normally. */
    disable() {
      interceptor.enabled = false;
      console.log(`${TAG} interception DISABLED — Enter now sends normally.`);
    },

    /** Resume blocking. */
    enable() {
      interceptor.enabled = true;
      console.log(`${TAG} interception ENABLED.`);
    },

    /** Guard-behaviour counters: {seen, intercepted, passed}. */
    stats: () => ({ ...interceptor.stats }),

    /** The currently held send, or null. Stands in for the modal's state. */
    get pending() {
      return pending;
    },

    /** Modal "Proceed" — replay the held send. */
    approve,

    /** Modal "Cancel" — drop it, leaving the text in the box. */
    discard,

    /** Where would send() route right now? Useful when replay misbehaves. */
    sendButton: () => adapter.findSendButton(),
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
