/**
 * ShadowGuard AI — Attachment interception
 * ================================================================
 * Stops a file reaching the page until the backend has read it.
 *
 * Images (Stage 3, via OCR) and PDFs (Stage 3b, via the text layer).
 * The two are handled identically HERE and differently on the server,
 * which is the right split: what a file is only matters once someone
 * has to read it, and this class's job is to stop it travelling.
 *
 * Two ways a file gets into ChatGPT, and they need different
 * treatment:
 *
 *   PASTE       Cmd+V with a screenshot on the clipboard. A `paste`
 *               event carries the file, and the event is CANCELLABLE —
 *               so preventDefault() means the page never sees it at
 *               all. This is the clean path.
 *
 *   FILE INPUT  clicking the + button and choosing a file. `change`
 *               is NOT cancellable in any useful sense: by the time it
 *               fires the browser has already put the file in
 *               `input.files`, and the page's own handler is next in
 *               line. Preventing it means clearing the input and
 *               stopping propagation, then putting the file BACK if
 *               the user approves.
 *
 * WHY THIS IS A SEPARATE FILE FROM interceptor.js
 * -----------------------------------------------
 * The text interceptor cancels a SEND. This one cancels an ATTACH,
 * which happens much earlier — the user is still composing, and there
 * may be no send for another minute. Merging them would mean one class
 * with two unrelated lifecycles and a `stats` object that counts two
 * different things in the same bucket.
 *
 * WHY WE INTERCEPT AT ATTACH RATHER THAN AT SEND
 * ----------------------------------------------
 * By send time the image is no longer ours to read: ChatGPT has
 * uploaded it to its own servers and the composer holds a thumbnail
 * referencing a remote id. **The bytes have already left the machine.**
 * Waiting for send would mean warning about a leak that has already
 * happened, which is exactly the observe-and-warn design D1 rejected.
 */

(() => {
  "use strict";

  const TAG = "[ShadowGuard]";

  /**
   * Watches for files being attached, and holds them.
   */
  class AttachmentInterceptor {
    /**
     * @param {object}   adapter       the platform adapter
     * @param {function} onIntercept   (file, context) => Promise<boolean>
     *        Resolves true to let the image through, false to drop it.
     */
    constructor(adapter, onIntercept) {
      this.adapter = adapter;
      this.onIntercept = onIntercept;
      this.enabled = true;

      this.stats = { seen: 0, intercepted: 0, passed: 0 };

      /**
       * Files we have already approved, by identity.
       *
       * The replay problem again, and a WeakSet solves it the way
       * `isTrusted` solves it for keystrokes: an approved File object
       * is re-dispatched, and without a marker we would intercept our
       * own replay forever. A WeakSet rather than a flag because it
       * holds no state that can get stuck — entries vanish when the
       * File is garbage collected, and there is nothing to reset after
       * an exception.
       */
      this.approved = new WeakSet();

      this._onPaste = this.#onPaste.bind(this);
      this._onChange = this.#onChange.bind(this);
    }

    start() {
      // Capture phase on window, for the same reason the text
      // interceptor uses it: at the target element, listeners fire in
      // registration order and the page registered first.
      window.addEventListener("paste", this._onPaste, true);
      window.addEventListener("change", this._onChange, true);
    }

    stop() {
      window.removeEventListener("paste", this._onPaste, true);
      window.removeEventListener("change", this._onChange, true);
    }

    /**
     * Inspectable attachments on a clipboard or file input.
     *
     * Two kinds, and the list is deliberately a WHITELIST rather than
     * "everything that is not text". A file type ShadowGuard cannot
     * read must travel the path it always did — holding back a .docx
     * we have no parser for would block the user's work to achieve
     * nothing, which is how a control gets uninstalled.
     *
     * What is here is what the backend can actually inspect:
     *
     *   image/*           Stage 3, via OCR
     *   application/pdf   Stage 3b, via the text layer
     */
    #inspectableIn(transfer) {
      if (!transfer) return [];

      return [...(transfer.files ?? [])].filter(
        (file) =>
          file.type.startsWith("image/") || file.type === "application/pdf"
      );
    }

    // =================================================================
    // Path 1 — pasting a screenshot
    // =================================================================

    /** @private */
    async #onPaste(event) {
      if (!this.enabled) return;

      const files = this.#inspectableIn(event.clipboardData);
      if (files.length === 0) return; // ordinary text paste, not ours

      this.stats.seen++;

      // Our own replay. Approved once, let through forever.
      if (files.every((file) => this.approved.has(file))) {
        this.stats.passed++;
        console.debug(`${TAG} paste passed through — already approved`);
        return;
      }

      // The cancellation. Everything after this point runs while the
      // page believes nothing was pasted.
      event.preventDefault();
      event.stopImmediatePropagation();

      this.stats.intercepted++;

      console.log(
        `%c ${TAG} ATTACHMENT HELD `,
        "background:#8a4b00; color:#fff; padding:2px 6px; border-radius:3px;",
        `${files.length} file(s) via paste`
      );

      for (const file of files) {
        const allowed = await this.onIntercept(file, { trigger: "paste" });

        if (allowed) this.#replayPaste(file, event.target);
      }
    }

    /**
     * Put an approved image back, as if the user had just pasted it.
     *
     * @private
     */
    #replayPaste(file, target) {
      this.approved.add(file);

      const transfer = new DataTransfer();
      transfer.items.add(file);

      // A real ClipboardEvent carrying the same file. The page's own
      // handler runs and does whatever it normally does — we are not
      // reimplementing its upload, only deciding whether it happens.
      const replay = new ClipboardEvent("paste", {
        clipboardData: transfer,
        bubbles: true,
        cancelable: true,
      });

      (target ?? document.activeElement ?? document.body).dispatchEvent(replay);

      console.log(`${TAG} approved image re-pasted (${file.name || "clipboard"})`);
    }

    // =================================================================
    // Path 2 — choosing a file
    // =================================================================

    /** @private */
    async #onChange(event) {
      if (!this.enabled) return;

      const input = event.target;
      if (!(input instanceof HTMLInputElement) || input.type !== "file") return;

      const files = this.#inspectableIn(input);
      if (files.length === 0) return;

      this.stats.seen++;

      if (files.every((file) => this.approved.has(file))) {
        this.stats.passed++;
        console.debug(`${TAG} file input passed through — already approved`);
        return;
      }

      // `change` cannot be cancelled the way `paste` can — the browser
      // has already populated `input.files`. Emptying the input is what
      // actually prevents the upload; stopping propagation is what
      // keeps the page's handler from having run first.
      event.stopImmediatePropagation();
      input.value = "";

      this.stats.intercepted++;

      console.log(
        `%c ${TAG} ATTACHMENT HELD `,
        "background:#8a4b00; color:#fff; padding:2px 6px; border-radius:3px;",
        `${files.length} file(s) via file input`
      );

      const approved = [];
      for (const file of files) {
        if (await this.onIntercept(file, { trigger: "file-input" })) {
          approved.push(file);
        }
      }

      if (approved.length > 0) this.#replayChange(input, approved);
    }

    /**
     * Put approved files back into the input and let the page notice.
     *
     * @private
     */
    #replayChange(input, files) {
      const transfer = new DataTransfer();

      for (const file of files) {
        this.approved.add(file);
        transfer.items.add(file);
      }

      // `input.files` is settable from a DataTransfer, which is the
      // only supported way to put a File into an input programmatically.
      input.files = transfer.files;

      input.dispatchEvent(new Event("change", { bubbles: true }));

      console.log(`${TAG} approved ${files.length} image(s) re-attached`);
    }
  }

  globalThis.SG = globalThis.SG || {};
  globalThis.SG.AttachmentInterceptor = AttachmentInterceptor;
})();
