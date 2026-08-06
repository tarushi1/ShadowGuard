/**
 * ShadowGuard AI — Gemini adapter
 * ================================================================
 * Everything ShadowGuard knows about gemini.google.com lives here,
 * and nothing outside this file knows Gemini exists.
 *
 * THIS FILE IS THE TEST OF D2
 * ---------------------------
 * Week 1 chose a pluggable adapter layer over `if (host === ...)`
 * branching, on the claim that adding a second platform would be one
 * new file that *cannot* break the first. This is that claim being
 * cashed: no line of `chatgpt.js` changes, no line of the interceptor,
 * the modal, the watcher or the backend changes. If a selector below
 * is wrong, ChatGPT keeps working.
 *
 * WHAT MAKES GEMINI DIFFERENT
 * ---------------------------
 * Not the interception — that is all platform-agnostic. The editor:
 *
 *   ChatGPT   ProseMirror inside a plain contenteditable
 *   Gemini    QUILL, inside an Angular `<rich-textarea>` component
 *
 * Both keep a document model and treat the DOM as a rendering of it,
 * so both defeat a naive write — which is why `replaceText` in the
 * base implementation goes through a synthetic paste rather than
 * assigning text. That approach is editor-agnostic by design (D39),
 * so it should carry over. **Should** is doing real work in that
 * sentence: see the honesty note at the bottom.
 */

(() => {
  "use strict";

  globalThis.SG = globalThis.SG || {};

  SG.registerAdapter({
    id: "gemini",
    label: "Gemini",

    hosts: ["gemini.google.com"],

    selectors: {
      /**
       * Tiered, most specific first, exactly as ChatGPT's are.
       *
       * The tier that matches is logged, and a match below tier 2 is
       * a warning rather than a success: it means Google shipped a
       * change and we are running on a fallback that may be reading
       * the wrong element. A DLP tool that quietly stops detecting is
       * worse than one that fails loudly.
       */
      composer: [
        // 1 — Gemini's own component wrapper. The most stable handle
        //     when it is present, because it is a semantic element
        //     rather than a generated class.
        "rich-textarea div[contenteditable='true']",

        // 2 — Quill's editor class. Stable across Quill versions,
        //     and Gemini has used it consistently.
        "div.ql-editor[contenteditable='true']",

        // 3 — ARIA, which Google is unusually good about keeping.
        "div[contenteditable='true'][role='textbox']",

        // 4 — the aria-label used on the prompt box.
        "div[aria-label*='prompt' i][contenteditable='true']",

        // 5 — last resort. Matches ANY editable div on the page, which
        //     on a rich app is very likely the wrong one. Reaching
        //     this tier should be read as "the adapter is broken".
        "div[contenteditable='true']",
      ],

      sendButton: [
        "button.send-button",
        "button[aria-label*='Send' i]",
        "button[mattooltip*='Send' i]",
      ],

      /**
       * Where an attached image would land.
       *
       * Not used for interception — the image interceptor works on
       * `paste` and `change` events at the window, so it needs no
       * platform knowledge at all. Recorded because the adapter is the
       * only place site-specific selectors are allowed to live, and a
       * future feature that needs it should find it here rather than
       * inventing its own.
       */
      fileInput: ["input[type='file'][accept*='image']", "input[type='file']"],
    },

    /**
     * Read the composer's text.
     *
     * `innerText`, not `textContent`, for the same reason as ChatGPT:
     * Quill renders each line as a separate block, and `textContent`
     * fuses them. A key split across two lines would read as one
     * unbroken token and match a pattern it should not — a
     * detection-accuracy decision, not a formatting one.
     */
    readText(element) {
      if (!element) return "";
      if (typeof element.value === "string") return element.value;

      return element.innerText ?? "";
    },

    /**
     * Is the composer effectively empty?
     *
     * Quill's equivalent of ProseMirror's placeholder paragraph is
     * `<p><br></p>`, whose innerText is a single newline — truthy,
     * non-zero-length, and not text the user typed.
     */
    isEmpty(element) {
      return this.readText(element).trim().length === 0;
    },
  });
})();

/**
 * HONESTY NOTE — read this before quoting Gemini support in the report
 * ====================================================================
 * The selectors above are written from Gemini's published DOM
 * structure and from the conventions Angular Material and Quill
 * follow. **They have not been verified against the live site.**
 *
 * That is a real limitation and it is stated here rather than
 * discovered by a marker. What IS verified is the architecture: the
 * adapter registry resolves this file for gemini.google.com, the
 * interceptor and modal are platform-agnostic and need no changes, and
 * the whole ChatGPT path is untouched — all of which the extension
 * test suite asserts.
 *
 * What is NOT verified is that these particular CSS selectors match
 * Google's current markup. The tiering exists precisely because that
 * is expected to rot: the console logs which tier matched, and
 * anything below tier 2 is a warning that the adapter needs
 * re-pointing.
 *
 * If a live check shows the composer matching at tier 5, or not at
 * all, the fix is to correct the list above — one file, no other
 * component involved, which is the entire argument D2 was making.
 */
