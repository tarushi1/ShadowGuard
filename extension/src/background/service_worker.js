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
importScripts("/src/shared/messages.js", "/src/shared/config.js");

const TAG = "[ShadowGuard:SW]";

// ---------------------------------------------------------------
// Identity
// ---------------------------------------------------------------
/**
 * Get this installation's user id, creating one on first use.
 *
 * chrome.storage rather than a module-level variable, and this is the
 * concrete payoff of the MV3 lifecycle lesson: Chrome terminates this
 * worker after ~30 seconds idle and every global is lost. A generated
 * id held in a `let` would silently become a NEW id on every wake-up,
 * so Stage 4's behavioural baseline would see a stream of first-time
 * users and never build a profile for anyone.
 *
 * Honest limitation for the report: this identifies an installation,
 * not a person, and the extension asserts it to the backend with
 * nothing to back it up. A real deployment would take identity from
 * an authenticated session. Fine for a prototype, but it means the
 * behavioural stage measures a browser profile rather than a human.
 */
async function getUserId() {
  const stored = await chrome.storage.local.get("userId");
  if (stored.userId) return stored.userId;

  const fresh = crypto.randomUUID();
  await chrome.storage.local.set({ userId: fresh });
  console.log(`${TAG} generated new user id: ${fresh}`);
  return fresh;
}

// ---------------------------------------------------------------
// Backend call
// ---------------------------------------------------------------
/**
 * Ask the backend to rule on a message.
 *
 * This function is the reason the service worker exists. A content
 * script cannot make this call: it runs with the PAGE's origin, so
 * the request would be a cross-origin one from chatgpt.com to
 * localhost and the browser would block it under CORS. The service
 * worker runs with the extension's own origin, and Chrome permits it
 * to reach anything listed in the manifest's host_permissions.
 *
 * That is not a workaround — it is Chrome deliberately funnelling all
 * extension network access through one auditable choke point.
 *
 * Never throws. Every failure mode returns { ok: false, error }, so
 * the caller has exactly one shape to handle and a network blip
 * cannot leave a message stuck in limbo.
 *
 * @param {object} payload  the AnalyzeRequest body
 * @returns {Promise<{ok: true, verdict: object} | {ok: false, error: string}>}
 */
/**
 * Send an image for analysis.
 *
 * WHY THE IMAGE ARRIVES HERE AS BASE64
 * ------------------------------------
 * `chrome.runtime.sendMessage` serialises through JSON, so a Blob or
 * an ArrayBuffer does not survive the content-script-to-worker hop —
 * it arrives as `{}`, silently, with no error anywhere.
 *
 * Which is worth noting against the decision NOT to use base64 for the
 * HTTP hop: the two hops have different constraints. Over HTTP we
 * control both ends and multipart avoids a 33% size penalty; over
 * Chrome's message channel there is no choice. The penalty is paid
 * once, in memory, on a local machine — and the request that leaves
 * this worker is still multipart.
 *
 * Takes the endpoint as an argument rather than branching inside,
 * because the two uploads differ only in where they are POSTed. The
 * base64 hop, the byte decoding, the multipart body, the timeout and
 * every failure path are identical, and duplicating them for PDFs
 * would mean two copies of the atob comment below that drift apart.
 *
 * @param {object} payload  {file_b64, mime, platform, trigger, ...}
 * @param {object} options  {path, label} which endpoint, and what to
 *                          call it in the logs
 */
async function requestFileAnalysis(payload, { path, label }) {
  const url = `${SG.CONFIG.BACKEND_URL}${path}`;

  // A far longer budget than text. OCR on a screenshot is ~200-400ms
  // of real CPU work against ~4ms for text, and aborting a request
  // that was going to succeed shows the user "offline" for a backend
  // that is running perfectly.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SG.CONFIG.IMAGE_TIMEOUT_MS);

  const startedAt = performance.now();

  try {
    // Base64 back to bytes. atob gives a binary string; Uint8Array.from
    // with charCodeAt is the standard way to get real bytes out of it,
    // because a JS string is UTF-16 and would otherwise corrupt every
    // byte above 0x7F — which is most of a PNG.
    const binary = atob(payload.file_b64);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));

    const form = new FormData();
    form.append("file", new Blob([bytes], { type: payload.mime }), "upload");
    form.append("user_id", payload.user_id);
    form.append("platform", payload.platform ?? "chatgpt");
    if (payload.trigger) form.append("trigger", payload.trigger);
    if (payload.client_version) form.append("client_version", payload.client_version);

    // No Content-Type header set by hand. fetch() derives it from the
    // FormData along with the multipart boundary, and setting it
    // manually omits that boundary — which the server rejects with a
    // parse error that looks nothing like its cause.
    const response = await fetch(url, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });

    if (!response.ok) {
      return { ok: false, error: `backend returned HTTP ${response.status}` };
    }

    const verdict = await response.json();
    const roundTripMs = Math.round(performance.now() - startedAt);

    console.log(
      `${TAG} ${label} verdict ${verdict.action} score=${verdict.score} ` +
      (verdict.pdf
        ? `pages=${verdict.pdf.pages_read}/${verdict.pdf.pages} ` +
          `layer=${verdict.pdf.has_text_layer} `
        : `ocr=${verdict.ocr?.words ?? 0}w conf=${verdict.ocr?.confidence ?? 0} `) +
      `server=${verdict.latency_ms}ms roundtrip=${roundTripMs}ms id=${verdict.request_id}`
    );

    return { ok: true, verdict, roundTripMs };
  } catch (error) {
    const reason =
      error.name === "AbortError"
        ? `no response within ${SG.CONFIG.IMAGE_TIMEOUT_MS}ms`
        : error.message;

    console.warn(`${TAG} ${label} analysis failed — ${reason}`);
    return { ok: false, error: reason };
  } finally {
    clearTimeout(timer);
  }
}


async function requestAnalysis(payload) {
  const url = `${SG.CONFIG.BACKEND_URL}${SG.CONFIG.ANALYZE_PATH}`;

  // fetch() has NO default timeout — a hung server means waiting
  // forever, which for us means a send that never resolves and a
  // composer the user can never clear. AbortController is the only
  // way to bound it.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SG.CONFIG.REQUEST_TIMEOUT_MS);

  const startedAt = performance.now();

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    // fetch() only rejects on NETWORK failure. A 500, a 422, a 404 all
    // resolve successfully with ok === false. Forgetting this check is
    // how an error page ends up being parsed as a verdict.
    if (!response.ok) {
      return { ok: false, error: `backend returned HTTP ${response.status}` };
    }

    const verdict = await response.json();
    const roundTripMs = Math.round(performance.now() - startedAt);

    // Round trip vs. the server's own latency_ms: the difference is
    // transport and serialisation overhead. Worth having both, because
    // the report needs to talk about what the USER waited for, not
    // just what the pipeline spent.
    console.log(
      `${TAG} verdict ${verdict.action} score=${verdict.score} ` +
      `server=${verdict.latency_ms}ms roundtrip=${roundTripMs}ms id=${verdict.request_id}`
    );

    return { ok: true, verdict, roundTripMs };
  } catch (error) {
    const reason =
      error.name === "AbortError"
        ? `no response within ${SG.CONFIG.REQUEST_TIMEOUT_MS}ms`
        : error.message;

    console.warn(`${TAG} analysis failed — ${reason}`);
    return { ok: false, error: reason };
  } finally {
    // Always clear the timer. Leaving it pending on the success path
    // would abort a controller nobody is listening to — harmless here,
    // but it leaks a timer per request and this worker handles every
    // message from every tab.
    clearTimeout(timer);
  }
}

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
/**
 * A stable key for "the chat this message belongs to".
 *
 * The tab id, and nothing else. Considered and rejected: hashing the
 * chat URL, which is more precise — a user switching between two chats
 * in one tab shares a key under this scheme — but requires the page to
 * hand us its URL and puts a value derived from the conversation's
 * identity on the wire. Sharing a key across chats in one tab is a
 * false-POSITIVE risk bounded by a five-minute expiry; the alternative
 * widens what is transmitted, which is the thing this project is least
 * willing to trade.
 *
 * Returns null when there is no tab, so the backend simply treats the
 * message as unprimed rather than grouping stray requests together.
 */
function conversationKey(sender) {
  const id = sender?.tab?.id;
  return typeof id === "number" ? `tab-${id}` : null;
}


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
        workerVersion: chrome.runtime.getManifest().version,
      });
      break;
    }

    case SG.MSG.ANALYZE_IMAGE: {
      (async () => {
        const userId = await getUserId();
        const result = await requestFileAnalysis(
          { ...message.payload, user_id: userId },
          { path: SG.CONFIG.ANALYZE_IMAGE_PATH, label: "image" }
        );
        sendResponse(result);
      })();

      break;
    }

    case SG.MSG.ANALYZE_PDF: {
      (async () => {
        const userId = await getUserId();
        const result = await requestFileAnalysis(
          { ...message.payload, user_id: userId },
          { path: SG.CONFIG.ANALYZE_PDF_PATH, label: "pdf" }
        );
        sendResponse(result);
      })();

      break;
    }

    case SG.MSG.ANALYZE_REQUEST: {
      // The content script deliberately does NOT send a user_id: it
      // has no access to chrome.storage, and identity is a property of
      // the installation rather than of one tab. Stamping it here keeps
      // it consistent across every tab and every conversation.
      //
      // The conversation key is stamped here for a STRONGER reason.
      // The backend carries context words between messages that share
      // one (D56), so a key the page could choose would let a hostile
      // site read another conversation's priming state by guessing it.
      // `sender.tab.id` is populated by Chrome, cannot be set by the
      // page, and never leaves this worker as anything but an integer
      // — no chat URL and no chat title is ever sent.
      (async () => {
        const userId = await getUserId();
        const result = await requestAnalysis({
          ...message.payload,
          user_id: userId,
          conversation_id: conversationKey(sender),
        });
        sendResponse(result);
      })();

      // The async IIFE above returns immediately, so sendResponse has
      // NOT been called yet when this switch finishes. `return true`
      // at the bottom of the listener is what keeps the channel open
      // for it. Without that, this is where you meet "The message port
      // closed before a response was received."
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
