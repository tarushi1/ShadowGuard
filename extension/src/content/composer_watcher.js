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

    /** Timestamp of the last upgrade sweep; see #check(). */
    this.lastUpgradeCheck = 0;
  }

  /**
   * How often to re-run the full selector chain while attached to a
   * degraded tier, looking for a better match that has since mounted.
   *
   * A compromise. Running the chain on every mutation would be
   * wasteful — a streaming reply fires hundreds per second. Never
   * running it means staying latched to a fallback element forever.
   * One sweep per second is invisible to the user and negligible next
   * to what the page itself is doing.
   */
  static UPGRADE_INTERVAL_MS = 1000;

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
   * Decide whether to (re-)attach. Runs on every DOM mutation.
   *
   * Three distinct situations, and an earlier version of this file
   * only handled the first two:
   *
   *   1. No handle yet          → discover
   *   2. Handle detached        → re-discover (SPA navigation)
   *   3. Handle attached, but a BETTER match has since mounted
   *                             → upgrade
   *
   * Case 3 is not hypothetical. ChatGPT renders a plain fallback
   * <textarea> first and hydrates the rich editor a moment later. A
   * watcher that only re-checks when its handle DIES stays latched to
   * that fallback forever — and it looks fine, because the fallback
   * is a real, visible, readable element. Silent attachment to the
   * wrong node is precisely the failure this class exists to prevent.
   *
   * `isConnected` is the precise question for case 2: "is this node
   * still in the document?" A single browser-maintained boolean, far
   * cheaper than walking parents or re-querying every time.
   *
   * Performance shape:
   *   - attached at tier 1  → one property read, return. The hot path,
   *                           since nothing can beat tier 1.
   *   - attached, degraded  → one property read plus, at most once a
   *                           second, a full chain sweep.
   *   - detached / missing  → full chain sweep.
   *
   * @private
   */
  #check() {
    const connected = this.current?.isConnected === true;

    // Best possible match, still attached. Nothing to look for.
    if (connected && this.lastTier === 1) return;

    if (connected) {
      // Degraded but working. Look for something better — throttled,
      // because a streaming reply fires hundreds of mutations a second
      // and each sweep is a handful of querySelector calls.
      const now = Date.now();
      if (now - this.lastUpgradeCheck < ComposerWatcher.UPGRADE_INTERVAL_MS) return;
      this.lastUpgradeCheck = now;
    }

    const found = this.find();
    if (!found) return;

    if (connected) {
      // Switch only for a STRICTLY better tier. Equal-or-worse means
      // we already have the best available, and swapping to a
      // different element at the same tier would churn listeners for
      // no gain.
      if (found.element === this.current || found.tier >= this.lastTier) return;

      console.log(
        `[ShadowGuard] composer UPGRADED tier ${this.lastTier} → ${found.tier} ` +
        `via "${found.selector}" — the real editor mounted after the fallback.`
      );
    }

    // Name the three cases distinctly. Reporting an upgrade as "SPA
    // navigation" sends you looking for a navigation that never
    // happened; a log that lies costs more than no log at all.
    const reason =
      this.current === null ? "initial" : connected ? "upgrade" : "reattach";

    this.current = found.element;

    if (found.tier !== this.lastTier) {
      // Tier changes are the early-warning signal for a platform
      // redesign, so they get a louder log than a plain re-attach.
      const severity = found.tier === 1 ? "log" : "warn";
      console[severity](
        `[ShadowGuard] composer matched at tier ${found.tier}/${this.adapter.selectors.composer.length} ` +
        `via "${found.selector}"` +
        (found.tier > 1 ? "  ← tier 1 no longer matches; ChatGPT markup may have changed" : "")
      );
      this.lastTier = found.tier;
    }

    this.onAttach(found.element, { ...found, reason });
  }
};
