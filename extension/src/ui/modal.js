/**
 * ShadowGuard AI — Warning modal
 * ================================================================
 * The user-facing half of the product. Everything else is plumbing;
 * this is what an evaluator actually sees.
 *
 * ---- Why a Shadow DOM ------------------------------------------
 * CSS is global. Every stylesheet on chatgpt.com applies to every
 * matching element, including ours — a `.card` rule of theirs, a
 * Tailwind preflight, a `button { all: unset }`. They change those
 * whenever they like and owe us no warning.
 *
 * For most extensions that is a cosmetic risk. For a DLP warning it
 * is a SECURITY risk: a dialog rendered invisible white-on-white, or
 * with its buttons collapsed to zero height, is a failure to warn,
 * and the user sends the API key. The reverse matters too — our
 * styles leaking out and breaking their layout is how an extension
 * gets uninstalled.
 *
 * attachShadow() gives us a subtree with its own style scope. Note
 * one leak: INHERITED properties (font-family, color, line-height)
 * still cross the boundary from the host, because inheritance is a
 * different mechanism from selector matching. `:host { all: initial }`
 * closes that.
 *
 * ---- Why mode: "closed" ----------------------------------------
 * With "open", `host.shadowRoot` is reachable by ANY script on the
 * page — so ChatGPT's own code, or another extension, could query our
 * Proceed button and click it. For a security control that is an
 * unacceptable default. "closed" returns null to everyone; only this
 * closure holds the reference.
 *
 * Honest limitation for the report: "closed" stops the page reaching
 * INTO our UI. It cannot stop the page finding and deleting the host
 * element. A content script is a guest and cannot fully defend
 * against a hostile host — this raises the cost, it does not
 * eliminate the risk.
 *
 * ---- Why <dialog>.showModal() ----------------------------------
 * ChatGPT uses high z-index values and, worse, STACKING CONTEXTS: an
 * ancestor with transform / filter / opacity < 1 traps its
 * descendants' z-index, so z-index: 999999 inside one can still paint
 * below a sibling with z-index: 1.
 *
 * showModal() promotes the element to the browser's TOP LAYER, which
 * renders above the whole page regardless of z-index or stacking
 * context. It also gives us focus trapping, Esc handling, ::backdrop
 * and aria-modal semantics for free. Hand-rolling correct focus
 * trapping is hard and frequently botched; use the platform.
 */

globalThis.SG = globalThis.SG || {};

globalThis.SG.WarningModal = class WarningModal {
  /** Deliberately odd tag name — must not collide with anything on the page. */
  static HOST_TAG = "shadowguard-modal-host";

  constructor() {
    /** @type {HTMLElement|null} */
    this.host = null;

    /** @type {ShadowRoot|null} kept private; mode:"closed" means nobody else has it */
    this.root = null;

    /** @type {HTMLDialogElement|null} */
    this.dialog = null;

    /** Resolver for the promise returned by show(). */
    this._resolve = null;

    /** Element that had focus before we opened, so we can restore it. */
    this._previousFocus = null;
  }

  /**
   * Build the host + shadow root once, lazily.
   *
   * Lazily because most page loads never show a warning, and creating
   * DOM we may never use is waste. Once, because rebuilding on every
   * warning would leak host elements into the page.
   *
   * @private
   */
  #ensureBuilt() {
    if (this.root) return;

    this.host = document.createElement(WarningModal.HOST_TAG);

    // Attach to <body> directly. Nesting the host inside ChatGPT's
    // React tree would put it at the mercy of their re-renders — they
    // would eventually remove a node they do not recognise.
    document.body.appendChild(this.host);

    this.root = this.host.attachShadow({ mode: "closed" });
    this.root.appendChild(this.#buildStyles());
    this.root.appendChild(this.#buildDialog());
  }

  /** @private */
  #buildStyles() {
    const style = document.createElement("style");

    style.textContent = `
      /* all: initial blocks INHERITED properties (font, colour,
         line-height) from crossing the shadow boundary. Without it
         our dialog silently adopts ChatGPT's typography, which
         changes without notice. */
      /* ---- Colour tokens -------------------------------------
         Every colour in this stylesheet is a variable, and the dark
         theme overrides ONLY the variables. That is not tidiness — it
         fixes a real bug and prevents its return.

         The first version put the prefers-color-scheme block near the
         top and the component rules below it. A media query adds NO
         SPECIFICITY, so with equal specificity the later rule wins:
         a plain '.detail' background further down silently overrode
         the dark-mode one, while the text colour still inherited the
         card's light foreground.

         (This comment previously used backticks. The whole stylesheet
         is a JS template literal, so they closed the string and every
         test in the suite failed at once — a reminder that CSS in a
         template literal is still JavaScript.)

         The result was light text on a light panel — a warning that
         could not be read, which is a FAILURE TO WARN rather than a
         cosmetic bug, and exactly the outcome D9 chose Shadow DOM to
         avoid. It shipped because nobody had opened the modal on a
         machine in dark mode.

         With tokens, a rule reads var(--sg-panel) wherever it sits in
         the file, so no amount of reordering can reintroduce this.
         Contrast ratios are noted because "looks fine on my screen" is
         how the original passed review. */
      :host {
        all: initial;

        --sg-surface:    #ffffff;   /* card background                  */
        --sg-text:       #17171a;   /* 17:1 on surface                  */
        --sg-panel:      #f2f2f5;   /* the detail block                 */
        --sg-muted:      #5a5a66;   /* 6.5:1 on panel — labels          */
        --sg-chip:       #e2e2ea;   /* entity pills                     */
        --sg-code-bg:    #eeeef2;   /* the message preview              */
        --sg-code-text:  #1f1f26;   /* 14:1 on code-bg                  */
        --sg-border:     rgba(0, 0, 0, 0.10);
        --sg-btn-bg:     #e2e2ea;
        --sg-btn-text:   #17171a;
      }

      /* The page's theme is not readable from here, so follow the OS
         preference. Matching ChatGPT's own toggle would mean reading
         their DOM, which is one more thing to break. */
      @media (prefers-color-scheme: dark) {
        :host {
          --sg-surface:    #1e1e20;
          --sg-text:       #f2f2f5;  /* 15.5:1 on surface */
          --sg-panel:      #2b2b30;
          --sg-muted:      #b4b4c0;  /* 7.4:1 on panel — was opacity:0.65, ~4:1 */
          --sg-chip:       #3d3d45;
          --sg-code-bg:    #121214;
          --sg-code-text:  #e2e2ea;  /* 15:1 on code-bg */
          --sg-border:     rgba(255, 255, 255, 0.12);
          --sg-btn-bg:     #3d3d45;
          --sg-btn-text:   #f2f2f5;
        }
      }

      dialog {
        padding: 0;
        border: none;
        background: transparent;
        max-width: 520px;
        width: calc(100vw - 32px);
        color: inherit;
      }

      dialog::backdrop {
        background: rgba(0, 0, 0, 0.55);
        backdrop-filter: blur(2px);
      }

      .card {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "Helvetica Neue", Arial, sans-serif;
        font-size: 14px;
        line-height: 1.5;
        background: var(--sg-surface);
        color: var(--sg-text);
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
      }

      .banner {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 20px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: #fff;
      }
      .banner .icon { font-size: 18px; line-height: 1; }

      /* Risk levels drive the banner colour. Deliberately distinct
         hues rather than shades of one, so the level is legible at a
         glance and to colour-blind users when paired with the text. */
      .level-low      { background: #2e7d32; }
      .level-medium   { background: #b26a00; }
      .level-high     { background: #c62828; }
      .level-critical { background: #6a1b9a; }

      /* Deliberately grey rather than a risk colour. "We could not
         check this" is a different statement from "this is dangerous",
         and colouring it like a threat would teach users to ignore
         real threats. */
      .level-offline  { background: #455a64; }

      .detail[hidden], .preview[hidden] { display: none; }

      .body { padding: 18px 20px 8px; }
      .message { margin: 0 0 14px; }

      .detail {
        background: var(--sg-panel);
        border: 1px solid var(--sg-border);
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 14px;
      }

      .row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 8px;
      }
      .row:last-child { margin-bottom: 0; }
      /* An explicit colour, not 'opacity: 0.65'. Opacity multiplies
         against whatever is behind it, so the same rule produced ~4:1
         contrast in dark mode and ~7:1 in light — a value that cannot
         be checked because it depends on the backdrop. */
      .label { color: var(--sg-muted); }
      .value { font-weight: 600; font-variant-numeric: tabular-nums; }

      .entities { display: flex; flex-wrap: wrap; gap: 6px; }
      .entity {
        background: var(--sg-chip);
        color: var(--sg-text);
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 12px;
        font-weight: 600;
      }

      .preview {
        background: var(--sg-code-bg);
        color: var(--sg-code-text);
        border: 1px solid var(--sg-border);
        border-radius: 8px;
        padding: 10px 12px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 12px;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 96px;
        overflow: auto;
        margin-bottom: 4px;
      }

      .actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        padding: 14px 20px 18px;
      }

      button {
        font: inherit;
        font-weight: 600;
        border: none;
        border-radius: 8px;
        padding: 9px 18px;
        cursor: pointer;
      }
      button:focus-visible { outline: 2px solid #4c8bf5; outline-offset: 2px; }

      .btn-secondary { background: var(--sg-btn-bg); color: var(--sg-btn-text); }
      .btn-danger    { background: #c62828; color: #fff; }
      .btn-danger[hidden] { display: none; }

      .footer {
        padding: 0 20px 14px;
        font-size: 11px;
        opacity: 0.55;
      }
    `;

    return style;
  }

  /** @private */
  #buildDialog() {
    const dialog = document.createElement("dialog");

    // Structure is static, so a template string is safe here — no
    // user-controlled data is interpolated. Everything that DOES come
    // from the user or the backend is written with textContent in
    // update(), never innerHTML. Interpolating the user's prompt into
    // markup would create an XSS hole inside our own security UI,
    // which would be a memorable way to fail a security project.
    dialog.innerHTML = `
      <div class="card" role="alertdialog" aria-labelledby="sg-title" aria-describedby="sg-msg">
        <div class="banner" id="sg-banner">
          <span class="icon" aria-hidden="true">&#9888;</span>
          <span id="sg-title">Sensitive content detected</span>
        </div>

        <div class="body">
          <p class="message" id="sg-msg"></p>

          <div class="detail" id="sg-detail">
            <div class="row">
              <span class="label">Risk score</span>
              <span class="value" id="sg-score"></span>
            </div>
            <div class="row">
              <span class="label">Detected</span>
              <span class="entities" id="sg-entities"></span>
            </div>
          </div>

          <div class="preview" id="sg-preview"></div>
        </div>

        <div class="actions">
          <button class="btn-secondary" id="sg-cancel" autofocus>Cancel</button>
          <button class="btn-danger" id="sg-proceed">Send anyway</button>
        </div>

        <div class="footer">ShadowGuard AI &middot; nothing has been sent</div>
      </div>
    `;

    dialog.querySelector("#sg-cancel").addEventListener("click", () => this.#settle("cancel"));
    dialog.querySelector("#sg-proceed").addEventListener("click", () => this.#settle("proceed"));

    // Esc fires `cancel` on a modal dialog. Let it through, but route
    // it via #settle so the promise always resolves — a dialog that
    // closes without resolving would leave the caller awaiting
    // forever, and the held message stuck in limbo.
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      this.#settle("cancel");
    });

    this.dialog = dialog;
    return dialog;
  }

  /**
   * Show the warning and wait for the user's decision.
   *
   * Returning a promise means the caller reads as a straight line:
   *
   *     const decision = await modal.show(verdict);
   *     if (decision === "proceed") replay();
   *
   * rather than threading callbacks through the interception path.
   *
   * @param {object}   verdict
   * @param {string}   verdict.level      low | medium | high | critical
   * @param {number}   verdict.score      0-100
   * @param {string[]} verdict.entities   detected entity type names
   * @param {string}   verdict.message    human-readable explanation
   * @param {string}   verdict.preview    excerpt of the text (already masked)
   * @param {boolean}  verdict.allowProceed  false hides the override button
   * @returns {Promise<"proceed"|"cancel">}
   */
  show(verdict) {
    this.#ensureBuilt();
    this.#update(verdict);

    // Remember where focus was so it can be restored on close. Without
    // this the user lands on <body> after dismissing and has to click
    // back into the composer — a small thing that feels broken.
    this._previousFocus = document.activeElement;

    this.dialog.showModal();

    // showModal() honours the `autofocus` attribute, which is on
    // Cancel. Override it explicitly when the variant wants a
    // different default, after opening so the dialog is focusable.
    this._defaultButton?.focus();

    return new Promise((resolve) => {
      this._resolve = resolve;
    });
  }

  /** @private */
  #update(verdict) {
    const {
      variant = "risk",
      level = "medium",
      score = 0,
      entities = [],
      message = "This message appears to contain sensitive information.",
      preview = "",
      redactedText = "",
      allowProceed = true,
    } = verdict ?? {};

    const $ = (id) => this.dialog.querySelector(id);
    const offline = variant === "offline";
    const redact = variant === "redact";

    $("#sg-banner").className = `banner level-${offline ? "offline" : level}`;

    $("#sg-title").textContent = offline
      ? "ShadowGuard is offline"
      : redact
        ? "Sensitive content can be removed"
        : level === "critical"
          ? "Blocked — critical content"
          : "Sensitive content detected";

    // textContent, not innerHTML. See the note in #buildDialog().
    $("#sg-msg").textContent = message;

    // There is no score and there are no entities when nothing ran.
    // Showing "0 / 100 (low)" would be an outright lie — it reads as
    // "we checked and it is fine", which is the opposite of the truth.
    $("#sg-detail").hidden = offline;

    if (!offline) {
      $("#sg-score").textContent = `${score} / 100 (${level})`;

      const list = $("#sg-entities");
      list.textContent = "";

      const names = entities.length > 0 ? entities : ["none"];
      for (const name of names) {
        const chip = document.createElement("span");
        chip.className = "entity";
        chip.textContent = name;
        list.appendChild(chip);
      }
    }

    // For a redaction the preview shows what WILL BE SENT, not what
    // the user typed. Showing the original here would be actively
    // misleading: they would be approving a message they had not been
    // shown, which is the whole thing re-confirmation exists to
    // prevent.
    const previewEl = $("#sg-preview");
    previewEl.textContent = redact ? redactedText : preview;
    previewEl.hidden = previewEl.textContent.length === 0;

    // At critical severity there is no override. That is a policy
    // decision surfaced in the UI: warn is advisory, block is not.
    const proceed = $("#sg-proceed");
    proceed.hidden = !allowProceed;
    proceed.textContent = offline
      ? "Send without checking"
      : redact
        ? "Send redacted"
        : "Send anyway";

    // Which button is focused IS the default action, because Enter
    // activates it. For a risk warning the safe default is Cancel. In
    // the offline case the chosen policy is fail-open, so the send
    // button carries the default — the dialog's job there is to
    // inform, not to obstruct.
    //
    // Redaction ALSO defaults to its action button, and for a reason
    // worth stating: the proposed send is strictly safer than what the
    // user typed. Defaulting to Cancel there would make the safe path
    // the effortful one, and people take the effortless path.
    this._defaultButton = (offline || redact) && allowProceed ? proceed : $("#sg-cancel");
  }

  /** @private */
  #settle(decision) {
    this.dialog?.close();

    // Restore focus before resolving, so the caller's replay lands
    // with the composer focused — the synthetic-Enter fallback in
    // adapter.send() depends on it.
    this._previousFocus?.focus?.();
    this._previousFocus = null;

    const resolve = this._resolve;
    this._resolve = null;
    resolve?.(decision);
  }

  /**
   * Is a decision currently outstanding?
   *
   * Callers need this because showModal() throws a DOMException if the
   * dialog is already open — and the user can absolutely press Enter
   * again behind an open warning.
   */
  get isOpen() {
    return this.dialog?.open === true;
  }

  /** Force-close without a user decision (SPA navigation, teardown). */
  dismiss() {
    if (this.isOpen) this.#settle("cancel");
  }
};
