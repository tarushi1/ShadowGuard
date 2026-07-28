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
 * At this stage (Task 3.3) the loop is closed end to end: every send
 * path is cancelled, the message is held, the service worker asks the
 * FastAPI backend for a verdict, and that verdict decides whether to
 * replay silently or raise the modal. When the backend cannot be
 * reached we fail OPEN but visibly — see SG.CONFIG.FAIL_MODE.
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

  const REASON_LABEL = {
    initial: "attached",
    reattach: "RE-attached (SPA navigation)",
    upgrade: "swapped to a better match (fallback → real editor)",
  };

  const watcher = new SG.ComposerWatcher(adapter, (element, info) => {
    composer = element;
    console.log(`${TAG} composer ${REASON_LABEL[info.reason]}:`, element);
  });

  watcher.start();

  // ---------------------------------------------------------------
  // Warning UI
  // ---------------------------------------------------------------
  // Built lazily on first show(), so a page that never triggers a
  // warning never pays for the DOM. Its shadow root is closed, which
  // means this instance is the only way to reach the dialog — page
  // scripts cannot query our Proceed button and click it.
  const modal = new SG.WarningModal();

  // A pending decision is meaningless once the conversation changes
  // underneath it: the composer we would replay into is gone.
  window.addEventListener("popstate", () => modal.dismiss());

  // ---------------------------------------------------------------
  // Send interception
  // ---------------------------------------------------------------
  // The interceptor asks for the composer through a getter rather
  // than receiving the element once. The watcher swaps `composer` out
  // on every SPA navigation, and a captured reference would go stale
  // silently — the listener would keep firing and keep comparing the
  // event target against a node that left the document minutes ago.
  const interceptor = new SG.Interceptor(adapter, () => composer, handleIntercept);

  interceptor.start();

  /**
   * The decision flow. Runs every time a send is cancelled.
   *
   *   intercept → analyse → allow?  ──yes──► replay silently
   *                           │
   *                           no
   *                           ▼
   *                      show modal → proceed → replay
   *                                 └ cancel  → discard
   *
   * @param {string} text
   * @param {{trigger: string}} context
   */
  async function handleIntercept(text, context) {
    // ---- Guard: a decision is already outstanding ---------------
    // The listener stays active while the modal is up, so the user
    // can press Enter again behind the dialog. showModal() throws a
    // DOMException on an already-open dialog, and overwriting
    // `pending` here would silently swap which message gets sent when
    // they click Proceed — the wrong text going out under an approval
    // the user gave for different text.
    if (modal.isOpen) {
      console.debug(`${TAG} a decision is already pending — ignoring this send.`);
      return;
    }

    pending = { text, trigger: context.trigger, at: Date.now() };

    // The content script cannot call the backend itself — it runs with
    // chatgpt.com's origin, so the request would be cross-origin and
    // blocked. The service worker runs with the extension's own origin
    // and is granted the hosts in host_permissions. This round trip
    // through the worker is Chrome's design, not a workaround.
    const result = await chrome.runtime.sendMessage({
      type: SG.MSG.ANALYZE_REQUEST,
      payload: {
        text,
        platform: adapter.id,
        trigger: context.trigger,
        client_version: VERSION,
      },
    });

    // ---- Backend unreachable: fail OPEN, but visibly ------------
    // See SG.CONFIG.FAIL_MODE for the reasoning. The short version:
    // being told a message was not inspected is meaningfully different
    // from being silently unprotected, and a control that bricks
    // ChatGPT whenever a laptop process is down gets uninstalled.
    if (!result?.ok) {
      console.warn(`${TAG} could not reach the backend — ${result?.error ?? "no response"}`);

      const decision = await modal.show({
        variant: "offline",
        message:
          "ShadowGuard could not reach its analysis service, so this message " +
          "has NOT been checked for sensitive content. Send it only if you " +
          "are confident it is safe.",
        preview: previewOf(text),
        allowProceed: SG.CONFIG.FAIL_MODE === "open",
      });

      if (decision === "proceed") approve();
      else discard();
      return;
    }

    const verdict = result.verdict;

    console.log(
      `${TAG} ${context.trigger} → ${verdict.action.toUpperCase()} ` +
      `(score ${verdict.score}, ${verdict.latency_ms}ms server / ` +
      `${result.roundTripMs}ms round trip, id ${verdict.request_id})`
    );

    // ---- allow: the user never learns we were here --------------
    // Every send now carries a real HTTP round trip, which is why the
    // backend's latency budget is a product requirement and not just a
    // number in the report.
    if (verdict.action === SG.ACTION.ALLOW) {
      approve({ silent: true });
      return;
    }

    // The backend returns entities as objects with offsets and never
    // the matched text. The modal only needs the type names, deduped —
    // three separate API keys should read as one "API_KEY" chip rather
    // than three identical ones.
    const entityTypes = [...new Set(verdict.entities.map((e) => e.type))];

    const decision = await modal.show({
      level: verdict.level,
      score: verdict.score,
      entities: entityTypes,
      message: verdict.message,
      preview: previewOf(text),

      // No override at block severity. warn is advisory; block is not.
      allowProceed: verdict.action !== SG.ACTION.BLOCK,
    });

    if (decision === "proceed") {
      approve();
    } else {
      discard();
    }
  }

  /** Trim the user's text to something that fits the modal. */
  function previewOf(text) {
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
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

    /** The modal instance. Its shadow root is closed, so this is the only handle. */
    modal,

    /**
     * Render the modal with sample data, to check it visually without
     * needing an interception or a backend. Task 2.5 replaces this
     * with the real flow.
     *
     * @param {string} level  low | medium | high | critical
     */
    demoModal(level = "high") {
      const samples = {
        low: {
          score: 18, entities: ["EMAIL_ADDRESS"],
          message: "Minor personal data detected. Sending is allowed.",
          preview: "contact me at alex@example.com",
        },
        medium: {
          score: 47, entities: ["PERSON", "PHONE_NUMBER"],
          message: "This message contains personal information about a named individual.",
          preview: "Priya Raman, +44 7700 900123, account overdue",
        },
        high: {
          score: 78, entities: ["AWS_ACCESS_KEY", "EMAIL_ADDRESS"],
          message: "This message appears to contain a cloud credential and personal data.",
          preview: "AKIA••••••••••••7Q2M  ·  ops@acme-internal.com",
        },
        critical: {
          score: 96, entities: ["OPENAI_API_KEY", "PRIVATE_KEY"],
          message:
            "A live secret was detected. Sending has been blocked and the attempt logged.",
          preview: "sk-proj-••••••••••••••••••••••••••••3f9A",
          allowProceed: false,
        },
      };

      return modal
        .show({ level, ...samples[level] })
        .then((decision) => {
          console.log(`${TAG} demo modal decision:`, decision);
          return decision;
        });
    },
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
