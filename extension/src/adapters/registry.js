/**
 * ShadowGuard AI — Adapter registry
 * ================================================================
 * Every generative-AI platform we support gets ONE adapter file
 * (chatgpt.js, gemini.js, ...). An adapter is the only place in the
 * codebase allowed to know site-specific details:
 *
 *   - which element is the message composer
 *   - which element is the send button
 *   - how to read the text out of that composer
 *   - how to send programmatically  (Week 2)
 *
 * Everything else — interception, the modal, the backend call — is
 * written against this interface and stays platform-agnostic.
 *
 * Why bother, when we only support one site today?
 * Because the alternative is `if (host === "chatgpt.com") { ... }`
 * sprinkled through the content script. By Week 6 that becomes an
 * unreadable thicket, and adding Gemini means editing five files
 * and hoping you found them all. With a registry, adding Gemini is
 * one new file plus one manifest line — and it CANNOT break ChatGPT,
 * because it does not touch any ChatGPT code path.
 *
 * Load order matters: this file must be listed in the manifest
 * before any adapter that calls registerAdapter().
 */

globalThis.SG = globalThis.SG || {};

/** @type {Array<object>} populated by each adapter file at load time. */
globalThis.SG.adapters = globalThis.SG.adapters || [];

/**
 * Register a platform adapter.
 *
 * Validation is deliberately strict and fails loudly. A half-written
 * adapter that registers successfully would fail later, deep inside
 * the interception path, with a confusing error. Better to refuse it
 * here where the message points straight at the offending file.
 *
 * @param {object}   adapter
 * @param {string}   adapter.id          machine name, e.g. "chatgpt"
 * @param {string}   adapter.label       human name for logs and the modal
 * @param {string[]} adapter.hosts       hostnames this adapter claims
 * @param {object}   adapter.selectors   tiered selector lists (see chatgpt.js)
 * @param {function} adapter.readText        (element) => string
 * @param {function} adapter.isEmpty         (element) => boolean
 * @param {function} adapter.matchSendButton (eventTarget) => {element,selector,tier}|null
 */
/**
 * Generic implementations every adapter gets unless it overrides them.
 * ================================================================
 *
 * These exist because the second adapter revealed the first one was
 * carrying code that was never platform-specific. `findSendButton` is
 * "try each selector in order, skip disabled and hidden ones";
 * `matchSendButton` is "does the click target sit inside one of these";
 * `send` is "click the button, or fall back to a synthetic Enter".
 * None of that knows anything about ChatGPT — it only knows about the
 * SELECTORS the adapter declares.
 *
 * With these here, adding a platform is genuinely one list of CSS
 * selectors, which is what D2 claimed and what the second adapter
 * turned from a claim into a fact.
 *
 * `chatgpt.js` deliberately keeps its own versions and is not touched
 * by this change. It has ChatGPT-specific handling — excluding the
 * STOP button that replaces send while a reply streams — and its
 * behaviour is pinned by tests that must keep passing unchanged. A
 * refactor that rewrites the working platform to tidy up the new one
 * risks the thing that works to benefit the thing that does not.
 */
const DEFAULTS = {
  /**
   * Find the composer, reporting which tier matched.
   *
   * The tier is the health signal: anything past the first couple of
   * entries means the site changed and we are running on a fallback
   * that may be reading the wrong element.
   */
  findComposer() {
    const tiers = this.selectors.composer;

    for (let index = 0; index < tiers.length; index++) {
      const element = document.querySelector(tiers[index]);

      // offsetParent is null for a display:none element. Sites keep
      // hidden fallback editors in the DOM, and latching onto one
      // means reading a box the user never types in.
      if (element && element.offsetParent !== null) {
        return { element, selector: tiers[index], tier: index + 1 };
      }
    }

    return null;
  },

  findSendButton() {
    const tiers = this.selectors.sendButton ?? [];

    for (let index = 0; index < tiers.length; index++) {
      for (const element of document.querySelectorAll(tiers[index])) {
        if (element.disabled) continue;
        if (element.offsetParent === null) continue;

        return { element, selector: tiers[index], tier: index + 1 };
      }
    }

    return null;
  },

  /**
   * Did this click land on the send button?
   *
   * `closest`, never `matches`. Clicks land on the <path> inside the
   * <svg> inside the <button>, so a `matches` check silently never
   * fires and the send goes through uninspected.
   */
  matchSendButton(target) {
    if (!(target instanceof Element)) return null;

    const tiers = this.selectors.sendButton ?? [];

    for (let index = 0; index < tiers.length; index++) {
      const element = target.closest(tiers[index]);
      if (element) return { element, selector: tiers[index], tier: index + 1 };
    }

    return null;
  },

  send(composer) {
    const button = this.findSendButton();

    if (button) {
      button.element.click();
      return { method: "button-click", detail: button.selector };
    }

    composer?.focus();
    composer?.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
        composed: true,
      })
    );

    return { method: "synthetic-enter", detail: "send button not found" };
  },

  /**
   * Replace the composer's contents, editor-agnostically.
   *
   * The approach is deliberately not tuned to any one editor: select
   * everything, let the editor's own selection state catch up, then
   * deliver the text through a PASTE, which every rich editor handles
   * as a first-class input path. Writing to the DOM and hoping the
   * editor notices does not work in ProseMirror, Quill, Slate or
   * Draft — see D39, which cost four attempts to learn.
   */
  async replaceText(element, text) {
    if (!element) return { ok: false, path: "no-element", selected: -1 };

    try {
      if (typeof element.value === "string") {
        const descriptor = Object.getOwnPropertyDescriptor(
          Object.getPrototypeOf(element), "value"
        );
        if (descriptor?.set) descriptor.set.call(element, text);
        else element.value = text;

        element.dispatchEvent(new Event("input", { bubbles: true }));
        return { ok: element.value === text, path: "native-setter", selected: -1 };
      }

      element.focus();

      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.selectAllChildren(element);
      document.dispatchEvent(new Event("selectionchange"));

      // The frame that matters. Editors sync their own selection from
      // `selectionchange`, which is delivered on the NEXT task — so a
      // paste dispatched in the same tick lands on a stale, collapsed
      // selection and APPENDS instead of replacing.
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const selected = String(window.getSelection() ?? "").length;

      const transfer = new DataTransfer();
      transfer.setData("text/plain", text);

      const handled = !element.dispatchEvent(
        new ClipboardEvent("paste", {
          clipboardData: transfer,
          bubbles: true,
          cancelable: true,
        })
      );

      if (!handled) document.execCommand("insertText", false, text);

      return {
        ok: this.readText(element).trim() === text.trim(),
        path: handled ? "paste" : "execCommand",
        selected,
      };
    } catch (error) {
      console.error("[ShadowGuard] replaceText failed:", error);
      return { ok: false, path: "threw", selected: -1 };
    }
  },
};


globalThis.SG.registerAdapter = function registerAdapter(adapter) {
  const required = [
    "id", "label", "hosts", "selectors",
    "readText", "isEmpty", "matchSendButton", "findSendButton", "send",
  ];
  // Defaults fill in anything the adapter did not declare, so the
  // required-key check runs against the MERGED object. An adapter that
  // only lists selectors is complete and valid.
  adapter = { ...DEFAULTS, ...adapter };

  const missing = required.filter((key) => adapter?.[key] === undefined);

  if (missing.length > 0) {
    throw new Error(
      `[ShadowGuard] adapter "${adapter?.id ?? "?"}" is missing: ${missing.join(", ")}`
    );
  }

  if (globalThis.SG.adapters.some((a) => a.id === adapter.id)) {
    // Duplicate registration means the file got listed twice in the
    // manifest. Harmless but always a mistake, so say so.
    console.warn(`[ShadowGuard] adapter "${adapter.id}" already registered — ignoring duplicate.`);
    return;
  }

  globalThis.SG.adapters.push(Object.freeze(adapter));
};

/**
 * Find the adapter responsible for a hostname.
 *
 * We match on exact hostname rather than a substring test. Substring
 * matching ("does the host contain 'chatgpt'?") is how extensions end
 * up running on `chatgpt.evil-phishing-site.com`. Exact equality is
 * both safer and easier to reason about.
 *
 * @param {string} hostname  e.g. location.hostname
 * @returns {object|null}
 */
globalThis.SG.resolveAdapter = function resolveAdapter(hostname) {
  return globalThis.SG.adapters.find((a) => a.hosts.includes(hostname)) ?? null;
};
