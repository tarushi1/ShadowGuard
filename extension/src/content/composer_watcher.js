/**
 * ShadowGuard AI — Composer watcher
 * ================================================================
 * Keeps a LIVE handle on the platform's message box.
 *
 * The problem it solves
 * ---------------------
 * ChatGPT is a Single Page Application. Clicking a conversation in
 * the sidebar changes the URL but never reloads the page: React tears
 * down the old DOM and builds new nodes. Our content script does not
 * re-run — it was injected once and is still holding a reference to
 * an element that is no longer in the document.
 *
 * A listener attached to a detached node still "works" in the sense
 * that it throws no error. It simply never fires again. In Week 2
 * that means Enter is not intercepted and the prompt reaches OpenAI
 * unchecked, with nothing in any log to say so.
 *
 * The fix has two halves:
 *   1. WAIT for the composer to appear (it may not exist at
 *      document_idle — React renders after Chrome fires that event).
 *   2. NOTICE when the handle goes stale and re-discover.
 *
 * Both are driven by one MutationObserver rather than a polling
 * timer: the browser tells us the instant the DOM changes, so we pay
 * nothing while the tab sits idle and we are never blind for a
 * polling interval right after navigation — which is exactly when a
 * user starts typing.
 */

globalThis.SG = globalThis.SG || {};

globalThis.SG.ComposerWatcher = class ComposerWatcher {
  /**
   * @param {object}   adapter    a registered site adapter
   * @param {function} onAttach   (element, info) => void — fires on first
   *                              discovery and after every re-discovery
   */
  constructor(adapter, onAttach) {
    this.adapter = adapter;
    this.onAttach = onAttach;

    /** @type {HTMLElement|null} the element we currently believe is live */
    this.current = null;

    /** @type {MutationObserver|null} */
    this.observer = null;

    /** Which selector tier matched last time — used to report degradation. */
    this.lastTier = null;
  }

  /**
   * Run the adapter's selector chain and return the first hit.
   *
   * Returning the tier index alongside the element is the whole point:
   * a result is not just "found / not found" but "found, and here is
   * how much we had to compromise to find it".
   *
   * @returns {{element: HTMLElement, selector: string, tier: number}|null}
   */
  find() {
    const chain = this.adapter.selectors.composer;

    for (let i = 0; i < chain.length; i++) {
      const element = document.querySelector(chain[i]);

      // offsetParent === null means the element is hidden (display:none
      // or an unmounted-but-not-removed node). ChatGPT keeps hidden
      // scaffolding around, and attaching to an invisible twin of the
      // real composer is a genuinely baffling bug to chase down.
      if (element && element.offsetParent !== null) {
        return { element, selector: chain[i], tier: i + 1 };
      }
    }

    return null;
  }

  /**
   * Begin watching. Safe to call once per page load.
   */
  start() {
    // Try immediately — on a warm SPA navigation the composer is
    // usually already there and we can skip waiting entirely.
    this.#check();

    // childList  : tell me about nodes added or removed
    // subtree    : ...anywhere beneath the root, not just direct children
    //
    // We deliberately do NOT set attributes:true or characterData:true.
    // Those fire on every keystroke and every style tweak — thousands
    // of callbacks a minute on a live chat page. We only care about
    // nodes appearing and disappearing.
    this.observer = new MutationObserver(() => this.#check());

    this.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  /** Stop watching and release the observer. */
  stop() {
    this.observer?.disconnect();
    this.observer = null;
  }

  /**
   * Re-discover if — and only if — our handle is missing or stale.
   *
   * `isConnected` is the precise question to ask: "is this node still
   * attached to the document?" It is a single boolean maintained by
   * the browser, far cheaper and more accurate than walking parents
   * or comparing against a fresh querySelector on every mutation.
   *
   * The early return matters for performance. This runs on EVERY DOM
   * mutation on a busy chat page — including every token streamed
   * into an assistant reply. In the common case it does one property
   * read and exits.
   *
   * @private
   */
  #check() {
    if (this.current?.isConnected) return;

    const found = this.find();
    if (!found) return;

    const wasStale = this.current !== null;
    this.current = found.element;

    if (found.tier !== this.lastTier) {
      // Tier changes are the early-warning signal for a ChatGPT
      // redesign, so they are worth a louder log than a re-attach.
      const severity = found.tier === 1 ? "log" : "warn";
      console[severity](
        `[ShadowGuard] composer matched at tier ${found.tier}/${this.adapter.selectors.composer.length} ` +
        `via "${found.selector}"` +
        (found.tier > 1 ? "  ← tier 1 no longer matches; ChatGPT markup may have changed" : "")
      );
      this.lastTier = found.tier;
    }

    this.onAttach(found.element, { ...found, reattached: wasStale });
  }
};
