/**
 * ShadowGuard AI — Send interceptor
 * ================================================================
 * Cancels a send before the platform can act on it.
 *
 * ---- The central constraint ------------------------------------
 * JavaScript has no way to PAUSE an event. There is no event.hold()
 * and no way to make a listener await something and have the browser
 * wait. An event handler is synchronous: the moment it returns, the
 * browser moves on.
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
 * ---- Why the listener lives on `window` in CAPTURE phase --------
 * A keydown travels window → document → ... → composer (capture),
 * then fires on the composer (target), then travels back up (bubble).
 *
 * addEventListener defaults to BUBBLE, which is too late: ChatGPT's
 * handler has already run and the message is gone.
 *
 * Attaching capture to the composer itself is ALSO not enough. At the
 * target element, capture and bubble listeners fire in REGISTRATION
 * order, and ChatGPT registered theirs first.
 *
 * Capture on an ancestor is the only placement the DOM spec
 * guarantees runs first, and `window` is the outermost ancestor on
 * the path. Hence: window, capture, first in line, every time.
 */

globalThis.SG = globalThis.SG || {};

globalThis.SG.Interceptor = class Interceptor {
  /**
   * @param {object}   adapter      registered site adapter
   * @param {function} getComposer  () => HTMLElement|null — the live handle
   * @param {function} onIntercept  (text, context) => void
   */
  constructor(adapter, getComposer, onIntercept) {
    this.adapter = adapter;
    this.getComposer = getComposer;
    this.onIntercept = onIntercept;

    /**
     * Master switch, flipped from the console via __sg.disable().
     *
     * This is a development ergonomics feature, not a product one:
     * while building, every Enter is blocked and ChatGPT becomes
     * unusable in that tab. Without an escape hatch you end up
     * disabling the whole extension to ask a question, then forget
     * to re-enable it and spend twenty minutes debugging code that
     * is not running.
     */
    this.enabled = true;

    /** Simple counter, useful when verifying the guards behave. */
    this.stats = { seen: 0, intercepted: 0, passed: 0 };

    // Bind once and keep the reference. removeEventListener only
    // detaches a listener if given the SAME function reference AND
    // the same capture flag — an inline arrow function can never be
    // removed, which is a classic source of leaked listeners.
    this._onKeyDown = this.#onKeyDown.bind(this);
  }

  start() {
    window.addEventListener("keydown", this._onKeyDown, { capture: true });
  }

  stop() {
    window.removeEventListener("keydown", this._onKeyDown, { capture: true });
  }

  /**
   * Decide whether this keystroke is a send we must cancel.
   *
   * Structured as a series of early returns rather than one nested
   * condition. Each guard names exactly one reason to let the event
   * through, so a misbehaving guard is easy to isolate — and the
   * reason strings double as documentation.
   *
   * @param {KeyboardEvent} event
   * @private
   */
  #onKeyDown(event) {
    if (!this.enabled) return;
    if (event.key !== "Enter") return;

    this.stats.seen++;

    const pass = (reason) => {
      this.stats.passed++;
      console.debug(`[ShadowGuard] Enter passed through — ${reason}`);
    };

    // ---- Guard 1: IME composition ------------------------------
    // For Japanese, Chinese and Korean input the user types phonetic
    // characters, the IME offers candidates, and Enter CONFIRMS the
    // selected candidate — it does not send the message. Swallowing
    // it would make the composer unusable for a large share of the
    // world's users.
    //
    // keyCode 229 is the legacy signal for "this key is being handled
    // by the IME". isComposing is the modern one. Browsers disagree
    // about which they set and when, so we check both.
    if (event.isComposing || event.keyCode === 229) {
      return pass("IME composition in progress");
    }

    // ---- Guard 2: Shift+Enter is a newline, not a send ----------
    if (event.shiftKey) {
      return pass("Shift+Enter (newline)");
    }

    // ---- Guard 3: synthetic events -----------------------------
    // isTrusted is true only for events the BROWSER generated from
    // real user input; anything dispatched from JavaScript is false
    // and cannot be forged.
    //
    // This is how Task 2.3's replay will work: after the user clicks
    // Proceed we dispatch a synthetic send, which this guard lets
    // straight through. No "am I currently replaying?" flag to set,
    // clear, and eventually leave stuck in the wrong state after an
    // exception.
    if (!event.isTrusted) {
      return pass("synthetic event (replay)");
    }

    // ---- Guard 4: is this actually the composer? ---------------
    // Enter in the conversation search box or a rename field is none
    // of our business. `contains` covers the composer's descendants,
    // because ProseMirror puts the caret inside a child <p>, so the
    // event target is usually NOT the composer element itself.
    const composer = this.getComposer();
    if (!composer) {
      return pass("no composer handle");
    }
    if (event.target !== composer && !composer.contains(event.target)) {
      return pass("target is outside the composer");
    }

    // ---- Guard 5: nothing to inspect ---------------------------
    // An empty composer cannot leak anything. Let the platform do
    // whatever it normally does with Enter on an empty box.
    if (this.adapter.isEmpty(composer)) {
      return pass("composer is empty");
    }

    // ---- This is a send. Cancel it. ----------------------------
    //
    // Order matters conceptually even though both are synchronous:
    //
    //   stopImmediatePropagation() — the event never reaches
    //     ChatGPT's own keydown handler, so their send code never
    //     runs. preventDefault() alone would NOT achieve this:
    //     sending is their JavaScript, not a browser default action,
    //     and preventDefault only cancels built-in behaviour.
    //
    //   preventDefault() — stops the BROWSER's default action for
    //     Enter in a contenteditable, which is inserting a newline.
    //     Without it the send is blocked but the user watches their
    //     text box grow a blank line on every attempt.
    //
    // Both are required. Neither is sufficient alone.
    event.stopImmediatePropagation();
    event.preventDefault();

    this.stats.intercepted++;

    const text = this.adapter.readText(composer);
    this.onIntercept(text, { event, composer, trigger: "enter-key" });
  }
};
