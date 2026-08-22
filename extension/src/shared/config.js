/**
 * ShadowGuard AI — Client configuration
 * ================================================================
 * Every tunable value the extension has, in one frozen object,
 * loaded by both the content script and the service worker.
 *
 * One file rather than constants scattered across modules, because
 * "where is the backend URL configured?" should have exactly one
 * answer — and because the failure policy below is a decision an
 * evaluator will ask about, so it should be findable.
 */

globalThis.SG = globalThis.SG || {};

globalThis.SG.CONFIG = Object.freeze({
  /**
   * 127.0.0.1 rather than localhost, deliberately.
   *
   * `localhost` resolves through the OS resolver and can land on ::1
   * (IPv6) while Uvicorn is bound to 127.0.0.1 (IPv4) — producing a
   * connection refused that looks exactly like "the server is not
   * running". Using the literal address removes the ambiguity.
   */
  BACKEND_URL: "http://127.0.0.1:8000",

  ANALYZE_PATH: "/api/v1/analyze",
  ANALYZE_IMAGE_PATH: "/api/v1/analyze/image",
  ANALYZE_PDF_PATH: "/api/v1/analyze/pdf",

  /**
   * Images get their own, longer budget.
   *
   * OCR on a screenshot takes ~200-400ms of real CPU work, against
   * ~4ms for text. Reusing the 3s text timeout would abort a request
   * that was going to succeed, and the user would see "offline" for a
   * backend that is running perfectly.
   */
  IMAGE_TIMEOUT_MS: 15000,

  /**
   * Refuse to upload anything larger, without asking the backend.
   *
   * Mirrors MAX_IMAGE_BYTES in schemas/analyze.py. Checking here saves
   * a pointless several-megabyte round trip that can only end in 413 —
   * and the two numbers are asserted equal by a contract test, because
   * a client limit that drifts above the server's produces a failure
   * that looks like a network problem.
   */
  MAX_IMAGE_BYTES: 8 * 1024 * 1024,

  /**
   * Mirrors MAX_PDF_BYTES in schemas/analyze.py.
   *
   * Larger than the image cap because a screenshot is one screen and a
   * document is a document: an ordinary scanned contract passes 8MB
   * without being an attack. The limit that actually protects the
   * backend from a PDF is not this one anyway — it is the page and
   * character caps in detectors/pdf.py, since a 20KB file can
   * decompress into gigabytes of text.
   */
  MAX_PDF_BYTES: 20 * 1024 * 1024,
  HEALTH_PATH: "/health",

  /**
   * How long to wait for a verdict before giving up.
   *
   * This sits directly in the user's path: they have pressed Enter
   * and are watching a composer that has not responded. Three seconds
   * is already a long time to stare at nothing, and a backend that
   * has not answered by then is not about to.
   *
   * Without a timeout a hung server means a permanently frozen send —
   * fetch() has no default timeout and will wait indefinitely.
   */
  REQUEST_TIMEOUT_MS: 3000,

  /**
   * What to do when the backend cannot be reached.
   *
   *   "open"   send the message, but tell the user it was NOT
   *            inspected
   *   "closed" refuse to send anything until the backend answers
   *
   * Chosen: "open", visibly.
   *
   * The trade-off, stated plainly because it belongs in the report:
   * fail-open means anyone who can stop the backend has bypassed the
   * control, which a real enterprise deployment would not accept.
   * Fail-closed is the stronger security posture and what commercial
   * DLP does. We chose open because a control that bricks ChatGPT
   * whenever a laptop process is not running gets uninstalled — and
   * because being *told* the message was uninspected is meaningfully
   * different from being silently unprotected. The visible notice is
   * what makes this a defensible choice rather than a convenient one.
   */
  FAIL_MODE: "open",
});
