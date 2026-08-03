/**
 * ShadowGuard AI — Send interceptor
 * ================================================================
 * Cancels a send before the platform can act on it.
 *
 * ---- The central constraint ------------------------------------
 * JavaScript has no way to PAUSE an event. There is no event.hold()
 * and no way to make the browser wait for an async handler. A
 * listener is synchronous: the moment it returns, the browser moves
 * on.
 *
 * Our backend call is asynchronous and takes tens of milliseconds.
 * So "inspect before sending" is not literally possible. What IS
 * possible:
 *
 *      cancel  →  stash the text  →  analyse  →  replay on approval
 *
 * Everything in Week 2 is a consequence of that. This file owns the
 * "cancel" half; Task 2.3 adds the "replay" half.
 *
 * ---- Why every listener is on `window` in CAPTURE phase --------
 * An event travels window → document → ... → target (capture), then
 * fires on the target, then travels back up (bubble).
 *
 * addEventListener defaults to BUBBLE, which is too late: the
 * platform's handler has already run and the message is gone.
 *
 * Attaching capture to the target itself is ALSO not enough. At the
 * target element, capture and bubble listeners fire in REGISTRATION
 * order, and the platform registered theirs first.
 *
 * Capture on an ancestor is the only placement the DOM spec
 * guarantees runs first, and `window` is the outermost ancestor on
 * the path. Hence: window, capture, first in line, every time.
 *
 * ---- Three ways to send, all closed ----------------------------
 *   keydown  Enter in the composer
 *   click    the send arrow  (or Space/Enter on the focused button,
 *            which the browser turns into a synthetic-but-trusted
 *            click for free)
 *   submit   the <form> around the composer — reachable without any
 *            click at all, via the browser's implicit-submission
 *            behaviour
 *
 * In practice cancelling the click means submit never fires. The
 * third listener is defence in depth: it means we do not have to be
 * right about which path the platform uses this month.
 */

globalThis.SG = globalThis.SG || {};

globalThis.SG.Interceptor = class Interceptor {
  /**
   * @param {object}   adapter      registered site adapter
   * @param {function} getComposer  () => HTMLElement|null — the live handle
   * @param {function} onIntercept  (text, context) => void
   */
  constructor(adapter, getComposer, onIntercept, options = {}) {
    this.adapter = adapter;
    this.getComposer = getComposer;
    this.onIntercept = onIntercept;

    /**
     * How we decide an event came from a real keystroke.
     *
     * A TEST SEAM, and the only one in this file. `isTrusted` is set
     * by the browser and cannot be forged — which is exactly why it is
     * the right guard in production and an impossible one to exercise
     * in a test. JSDOM defines it as a non-configurable own property,
     * so every event a test can construct looks synthetic, and the
     * most important behaviour in this class — "a real Enter IS
     * intercepted" — could not be asserted at all.
     *
     * This does not weaken the guard. Production uses the default,
     * which reads the native flag; the override exists only for a test
     * that holds the instance. Page script cannot reach this object —
     * it lives in the extension's isolated world — so there is nothing
     * here for a hostile page to substitute.
     */
    this.isTrusted = options.isTrusted ?? ((event) => event.isTrusted === true);

    /**
     * Master switch, flipped from the console via __sg.disable().
     *
     * A development ergonomics feature, not a product one: with every
     * send path closed, the tab cannot send anything at all until
     * Task 2.3 lands. Without an escape hatch you end up disabling
     * the whole extension to ask a question, then forget to
     * re-enable it and spend twenty minutes debugging code that is
     * not running.
     */
    this.enabled = true;

    /** Counters, useful when verifying the guards actually fire. */
    this.stats = { seen: 0, intercepted: 0, passed: 0 };

    /** Which send-button selector tier matched last — see #onClick. */
    this.lastButtonTier = null;

    // Bind once and keep the references. removeEventListener only
    // detaches a listener given the SAME function reference AND the
    // same capture flag — an inline arrow function can never be
    // removed, a classic source of leaked listeners.
    this._onKeyDown = this.#onKeyDown.bind(this);
    this._onClick = this.#onClick.bind(this);
    this._onSubmit = this.#onSubmit.bind(this);
  }

  start() {
    const opts = { capture: true };
    window.addEventListener("keydown", this._onKeyDown, opts);
    window.addEventListener("click", this._onClick, opts);
    window.addEventListener("submit", this._onSubmit, opts);
  }

  stop() {
    const opts = { capture: true };
    window.removeEventListener("keydown", this._onKeyDown, opts);
    window.removeEventListener("click", this._onClick, opts);
    window.removeEventListener("submit", this._onSubmit, opts);
  }

  // =================================================================
  // Shared decision path
  // =================================================================

  /**
   * Guards that apply to EVERY send path, whatever triggered it.
   *
   * Kept in one place deliberately. Duplicating five guards across
   * three handlers is how one of them quietly drifts out of sync —
   * and a guard that is right in two handlers and wrong in the third
   * produces a bug that only reproduces when the user sends one
   * particular way.
   *
   * @returns {HTMLElement|null} the composer if we should proceed
   * @private
   */
  #sharedGuards(event) {
    if (!this.enabled) return null;

    const pass = (reason) => {
      this.stats.passed++;
      console.debug(`[ShadowGuard] ${event.type} passed through — ${reason}`);
      return null;
    };

    // ---- Synthetic events --------------------------------------
    // isTrusted is true only for events the BROWSER generated from
    // real user input; anything dispatched from JavaScript is false,
    // and it cannot be forged.
    //
    // This is how Task 2.3's replay works: after the user clicks
    // Proceed we dispatch a synthetic send, which this guard lets
    // straight through. No "am I currently replaying?" flag to set,
    // clear, and eventually leave stuck in the wrong state after an
    // exception.
    if (!this.isTrusted(event)) return pass("synthetic event (replay)");

    const composer = this.getComposer();
    if (!composer) return pass("no composer handle");

    // ---- Nothing to inspect ------------------------------------
    // An empty composer cannot leak anything, so let the platform do
    // whatever it normally does.
    if (this.adapter.isEmpty(composer)) return pass("composer is empty");

    return composer;
  }

  /**
   * Cancel the send and hand the text to the orchestrator.
   *
   * Both calls are required and they do different jobs:
   *
   *   stopImmediatePropagation() — the event never reaches the
   *     platform's own handler, so their send code never runs.
   *     preventDefault() alone would NOT achieve this: sending is
   *     their JavaScript, not a browser default action, and
   *     preventDefault only cancels built-in behaviour.
   *
   *   preventDefault() — cancels the BROWSER's default action:
   *     inserting a newline for Enter in a contenteditable, or
   *     submitting the form for a click on a submit button. Without
   *     it the send is blocked but the user watches their text box
   *     grow a blank line on every attempt.
   *
   * Neither is sufficient alone.
   *
   * @private
   */
  #cancel(event, composer, trigger) {
    event.stopImmediatePropagation();
    event.preventDefault();

    this.stats.intercepted++;

    const text = this.adapter.readText(composer);
    this.onIntercept(text, { event, composer, trigger });
  }

  // =================================================================
  // Path 1 — Enter in the composer
  // =================================================================

  /** @private */
  #onKeyDown(event) {
    if (event.key !== "Enter") return;
    if (!this.enabled) return;

    this.stats.seen++;

    const pass = (reason) => {
      this.stats.passed++;
      console.debug(`[ShadowGuard] Enter passed through — ${reason}`);
    };

    // ---- IME composition ---------------------------------------
    // For Japanese, Chinese and Korean input the user types phonetic
    // characters, the IME offers candidates, and Enter CONFIRMS the
    // selected candidate — it does not send. Swallowing it would
    // make the composer unusable for a large share of the world's
    // users.
    //
    // keyCode 229 is the legacy "this key belongs to the IME" signal;
    // isComposing is the modern one. Browsers disagree about which
    // they set and when, so we check both.
    if (event.isComposing || event.keyCode === 229) {
      return pass("IME composition in progress");
    }

    // Shift+Enter is a newline, not a send.
    if (event.shiftKey) return pass("Shift+Enter (newline)");

    const composer = this.#sharedGuards(event);
    if (!composer) return;

    // Enter in the conversation search box or a rename field is none
    // of our business. `contains` covers descendants, because
    // ProseMirror puts the caret inside a child <p> — so the event
    // target is usually NOT the composer element itself.
    if (event.target !== composer && !composer.contains(event.target)) {
      return pass("target is outside the composer");
    }

    this.#cancel(event, composer, "enter-key");
  }

  // =================================================================
  // Path 2 — clicking the send arrow
  // =================================================================

  /**
   * Delegated click handling.
   *
   * We never hold a reference to the send button. The platform
   * remounts it constantly — it swaps to a STOP button while a reply
   * streams — so a stored reference would go dead and block nothing.
   * Instead we ask, per click, "did this originate inside a send
   * button?" That is immune to remounting by construction.
   *
   * @private
   */
  #onClick(event) {
    if (!this.enabled) return;

    // This runs on EVERY click anywhere on the page, so the common
    // case must be cheap: one closest() call that misses, and out.
    const match = this.adapter.matchSendButton(event.target);
    if (!match) return;

    this.stats.seen++;

    if (match.tier !== this.lastButtonTier) {
      const severity = match.tier === 1 ? "log" : "warn";
      console[severity](
        `[ShadowGuard] send button matched at tier ${match.tier}/` +
        `${this.adapter.selectors.sendButton.length} via "${match.selector}"` +
        (match.tier > 1 ? "  ← tier 1 no longer matches; markup may have changed" : "")
      );
      this.lastButtonTier = match.tier;
    }

    const composer = this.#sharedGuards(event);
    if (!composer) return;

    this.#cancel(event, composer, "send-button");
  }

  // =================================================================
  // Path 3 — form submission
  // =================================================================

  /**
   * Backstop for submissions that never involved a click.
   *
   * A <form> can be submitted by the browser's implicit-submission
   * behaviour (Enter inside certain inputs) with no click event at
   * all. Cancelling the click above normally means this never fires;
   * it exists so that a platform change to a submit-driven flow does
   * not silently open a hole.
   *
   * We only act on the form that actually contains our composer —
   * the page has other forms (search, settings) and hijacking those
   * would break the app.
   *
   * @private
   */
  #onSubmit(event) {
    if (!this.enabled) return;

    const composer = this.getComposer();
    if (!composer) return;

    const form = event.target;
    if (typeof form?.contains !== "function" || !form.contains(composer)) return;

    this.stats.seen++;

    const guarded = this.#sharedGuards(event);
    if (!guarded) return;

    this.#cancel(event, guarded, "form-submit");
  }
};
