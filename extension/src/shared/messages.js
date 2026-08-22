/**
 * ShadowGuard AI — Shared message vocabulary
 * ================================================================
 * The single source of truth for every message type exchanged
 * between the content script (World 2) and the service worker
 * (World 3).
 *
 * This file is deliberately loaded by BOTH worlds:
 *   - content script : listed in manifest content_scripts, before content.js
 *   - service worker : pulled in with importScripts()
 *
 * Why a shared file instead of typing the strings inline?
 * Message routing is string matching. If the sender says "ANALYSE"
 * and the receiver checks for "ANALYZE", Chrome does not raise an
 * error — the message is simply delivered to nobody and the caller
 * waits forever. That failure is invisible and painful to trace.
 * Referencing SG.MSG.ANALYZE_REQUEST instead turns the same typo
 * into an immediate `undefined`, which is loud and obvious.
 *
 * We attach to globalThis rather than using ES module exports
 * because classic content scripts cannot use `import`, while
 * globalThis works identically in a page context and a worker.
 */

globalThis.SG = globalThis.SG || {};

/**
 * The four verdicts the risk engine can return.
 *
 * Defined here, in the shared vocabulary, because from Week 3 these
 * strings travel from Python → service worker → content script → UI.
 * Every layer must agree on their spelling, and a mismatch fails the
 * same silent way a mistyped message type does.
 *
 *   allow   send it, say nothing
 *   warn    show the modal, user may override
 *   redact  replace the sensitive spans, then send      (Week 5)
 *   block   show the modal with no override, and log
 */
globalThis.SG.ACTION = Object.freeze({
  ALLOW: "allow",
  WARN: "warn",
  REDACT: "redact",
  BLOCK: "block",
});

globalThis.SG.MSG = Object.freeze({
  // Task 1.4 — liveness check across the bridge.
  PING: "SG_PING",
  PONG: "SG_PONG",

  // Week 3 — the real traffic. Declared now so the protocol is
  // visible as a whole; nothing handles these yet.
  ANALYZE_REQUEST: "SG_ANALYZE_REQUEST",
  ANALYZE_RESULT: "SG_ANALYZE_RESULT",

  /**
   * An image the user is trying to attach.
   *
   * A separate type rather than a flag on ANALYZE_REQUEST, because the
   * payload, the endpoint and the failure modes are all different — and
   * a message type that means two things is the kind of ambiguity this
   * file exists to prevent.
   */
  ANALYZE_IMAGE: "SG_ANALYZE_IMAGE",

  /**
   * A PDF to inspect. Separate from ANALYZE_IMAGE for the same reason
   * that one is separate from ANALYZE_REQUEST: a different endpoint, a
   * different size limit, and a different shape of answer coming back
   * (pages and channels rather than dimensions and confidence).
   */
  ANALYZE_PDF: "SG_ANALYZE_PDF",
  USER_DECISION: "SG_USER_DECISION",
});

// Object.freeze prevents accidental mutation at runtime. In an
// isolated world other extensions cannot reach this, but freezing
// also documents intent: these are constants, not configuration.
