/**
 * ShadowGuard AI — Content Script
 * ================================================================
 * Runs INSIDE the page of a supported generative-AI platform.
 * This file is the orchestrator: it owns no site-specific knowledge
 * (that lives in src/adapters/) and no discovery logic (that lives
 * in composer_watcher.js). It wires the pieces together.
 *
 * What this file can do:
 *   - read and modify the page's DOM
 *   - render our warning modal over the page          (Week 2)
 *   - exchange JSON messages with the service worker
 *
 * What this file can NOT do:
 *   - see the page's own JavaScript variables (isolated world)
 *   - call our backend directly (that is the service worker's job)
 *
 * At this stage (Task 3.3) the loop is closed end to end: every send
 * path is cancelled, the message is held, the service worker asks the
 * FastAPI backend for a verdict, and that verdict decides whether to
 * replay silently or raise the modal. When the backend cannot be
 * reached we fail OPEN but visibly — see SG.CONFIG.FAIL_MODE.
 */

(() => {
  "use strict";

  const TAG = "[ShadowGuard]";

  // Single source of truth for the version: read it from the manifest
  // rather than hardcoding a constant in each file. Duplicated version
  // strings drift, and then a console log tells you that you are
  // running code you replaced twenty minutes ago.
  const VERSION = chrome.runtime.getManifest().version;

  /**
   * How long to wait before checking that a replayed send actually
   * took effect. Long enough for React to re-render and clear the
   * composer, short enough that a failure is reported while the user
   * is still looking at the screen.
   */
  const VERIFY_DELAY_MS = 500;

  /**
   * How many times one send may be re-analysed before we refuse it.
   *
   * The composer stays editable while a decision is outstanding, so
   * the text can change between the moment it was inspected and the
   * moment it is replayed. Every replay path therefore re-reads the
   * composer and, if it differs, analyses the NEW text instead of
   * sending it (bypass B1).
   *
   * That is a loop: analyse, ask, edit, analyse again. A person will
   * stop after one or two rounds. A hostile page rewriting the
   * composer on every dialog close would not, and each round costs an
   * HTTP request — so the loop is bounded and the bound fails CLOSED.
   * Three is chosen to be past any plausible human sequence (fix a
   * typo, remove the flagged value) while still terminating quickly.
   */
  const MAX_RECHECKS = 3;

  /**
   * The send we cancelled and are holding, or null.
   * Declared here so the interceptor callback and the approve/discard
   * helpers below all close over the same binding.
   * @type {{text: string, trigger: string, at: number}|null}
   */
  let pending = null;

  /**
   * Guard against running twice in the same isolated world.
   *
   * `window` here is the ISOLATED world's window, not the page's, so
   * this property is invisible to ChatGPT's own code. A normal reload
   * gives us a fresh world, but any programmatic re-injection we add
   * later would reuse it — and two watchers would mean two warning
   * modals for one keystroke.
   */
  if (window.__shadowguardActive) {
    console.debug(`${TAG} already active in this frame — skipping re-injection.`);
    return;
  }
  window.__shadowguardActive = true;

  console.log(
    `%c ${TAG} ACTIVE `,
    "background:#0a7d47; color:#fff; padding:2px 6px; border-radius:3px; font-weight:bold;"
  );
  console.log(`${TAG} version    : ${VERSION}`);
  console.log(`${TAG} host       : ${location.hostname}`);
  console.log(`${TAG} frame      : ${window === window.top ? "top-level" : "iframe"}`);

  // ---------------------------------------------------------------
  // Adapter selection
  // ---------------------------------------------------------------
  // The manifest already restricts us to supported hosts, so a miss
  // here means the manifest and the adapter's `hosts` list disagree.
  // Bail loudly rather than run half-configured.
  const adapter = SG.resolveAdapter(location.hostname);

  if (!adapter) {
    console.error(
      `${TAG} no adapter registered for "${location.hostname}". ` +
      `Check that the manifest "matches" and the adapter "hosts" list agree.`
    );
    return;
  }
  console.log(`${TAG} adapter    : ${adapter.label} (${adapter.id})`);

  // ---------------------------------------------------------------
  // Composer discovery
  // ---------------------------------------------------------------
  /** @type {HTMLElement|null} the live composer, maintained by the watcher */
  let composer = null;

  const REASON_LABEL = {
    initial: "attached",
    reattach: "RE-attached (SPA navigation)",
    upgrade: "swapped to a better match (fallback → real editor)",
  };

  const watcher = new SG.ComposerWatcher(adapter, (element, info) => {
    composer = element;
    console.log(`${TAG} composer ${REASON_LABEL[info.reason]}:`, element);
  });

  watcher.start();

  // ---------------------------------------------------------------
  // Warning UI
  // ---------------------------------------------------------------
  // Built lazily on first show(), so a page that never triggers a
  // warning never pays for the DOM. Its shadow root is closed, which
  // means this instance is the only way to reach the dialog — page
  // scripts cannot query our Proceed button and click it.
  const modal = new SG.WarningModal();

  // A pending decision is meaningless once the conversation changes
  // underneath it: the composer we would replay into is gone.
  window.addEventListener("popstate", () => modal.dismiss());

  // ---------------------------------------------------------------
  // Send interception
  // ---------------------------------------------------------------
  // The interceptor asks for the composer through a getter rather
  // than receiving the element once. The watcher swaps `composer` out
  // on every SPA navigation, and a captured reference would go stale
  // silently — the listener would keep firing and keep comparing the
  // event target against a node that left the document minutes ago.
  const interceptor = new SG.Interceptor(adapter, () => composer, handleIntercept);

  interceptor.start();

  /**
   * The decision flow. Runs every time a send is cancelled.
   *
   *   intercept → analyse → allow?  ──yes──► replay silently
   *                           │
   *                           no
   *                           ▼
   *                      show modal → proceed → replay
   *                                 └ cancel  → discard
   *
   * @param {string} text
   * @param {{trigger: string}} context
   */
  async function handleIntercept(text, context, depth = 0) {
    // ---- Guard: a decision is already outstanding ---------------
    // The listener stays active while the modal is up, so the user
    // can press Enter again behind the dialog. showModal() throws a
    // DOMException on an already-open dialog, and overwriting
    // `pending` here would silently swap which message gets sent when
    // they click Proceed — the wrong text going out under an approval
    // the user gave for different text.
    if (modal.isOpen) {
      console.debug(`${TAG} a decision is already pending — ignoring this send.`);
      return;
    }

    pending = { text, trigger: context.trigger, at: Date.now() };

    // The content script cannot call the backend itself — it runs with
    // chatgpt.com's origin, so the request would be cross-origin and
    // blocked. The service worker runs with the extension's own origin
    // and is granted the hosts in host_permissions. This round trip
    // through the worker is Chrome's design, not a workaround.
    const result = await ask({
      type: SG.MSG.ANALYZE_REQUEST,
      payload: {
        text,
        platform: adapter.id,
        trigger: context.trigger,
        client_version: VERSION,
      },
    });

    // ---- Backend unreachable: fail OPEN, but visibly ------------
    // See SG.CONFIG.FAIL_MODE for the reasoning. The short version:
    // being told a message was not inspected is meaningfully different
    // from being silently unprotected, and a control that bricks
    // ChatGPT whenever a laptop process is down gets uninstalled.
    if (!result?.ok) {
      console.warn(`${TAG} could not reach the backend — ${result?.error ?? "no response"}`);

      const decision = await modal.show({
        variant: "offline",
        // Two different failures need two different sentences. "The
        // service is down" and "this tab has lost the extension" call
        // for different actions from the user, and a single generic
        // message would leave them retrying something that cannot work.
        message: result?.disconnected
          ? "ShadowGuard was reloaded or updated while this page was open, so " +
            "this tab is no longer protected and the message has NOT been " +
            "checked. Reload the page to restore protection."
          : "ShadowGuard could not reach its analysis service, so this message " +
            "has NOT been checked for sensitive content. Send it only if you " +
            "are confident it is safe.",
        preview: previewOf(text),
        allowProceed: SG.CONFIG.FAIL_MODE === "open",
      });

      if (decision !== "proceed") {
        discard();
        return;
      }

      // Checked even here, where nothing was analysed. The user is
      // approving an UNINSPECTED send, but they are approving THIS
      // text — replaying something they edited afterwards would still
      // be sending a message they never agreed to.
      if (await replayIsStillValid(depth)) approve();
      return;
    }

    const verdict = result.verdict;

    console.log(
      `${TAG} ${context.trigger} → ${verdict.action.toUpperCase()} ` +
      `(score ${verdict.score}, ${verdict.latency_ms}ms server / ` +
      `${result.roundTripMs}ms round trip, id ${verdict.request_id})`
    );

    // ---- allow: the user never learns we were here --------------
    // Every send now carries a real HTTP round trip, which is why the
    // backend's latency budget is a product requirement and not just a
    // number in the report.
    if (verdict.action === SG.ACTION.ALLOW) {
      // The window here is small — one HTTP round trip — but it is not
      // zero, and it is the one window the user is given no dialog to
      // notice. A verdict of `allow` for text that is no longer in the
      // box is not a verdict about anything.
      if (await replayIsStillValid(depth)) approve({ silent: true });
      return;
    }

    // The backend returns entities as objects with offsets and never
    // the matched text. The modal only needs the type names, deduped —
    // three separate API keys should read as one "API_KEY" chip rather
    // than three identical ones.
    const entityTypes = [...new Set(verdict.entities.map((e) => e.type))];

    // ---- redact: offer a safer version --------------------------
    // The only path where ShadowGuard rewrites what the user typed,
    // and it never does so without showing them the exact result
    // first. Silently editing someone's message and sending it would
    // be a worse betrayal of trust than either warning or blocking.
    //
    // Guarded on `redacted_text` actually being present: if the server
    // says `redact` but sends nothing to substitute, there is no safe
    // action available, so fall through to an ordinary warning rather
    // than inventing one.
    const redacting =
      verdict.action === SG.ACTION.REDACT && Boolean(verdict.redacted_text);

    if (verdict.action === SG.ACTION.REDACT && !redacting) {
      console.warn(`${TAG} server asked for redaction but sent no replacement — warning instead.`);
    }

    const decision = await modal.show({
      variant: redacting ? "redact" : "risk",
      level: verdict.level,
      score: verdict.score,
      entities: entityTypes,
      message: verdict.message,
      preview: previewOf(text),
      redactedText: redacting ? previewOf(verdict.redacted_text) : "",

      // No override at block severity. warn is advisory; block is not.
      allowProceed: verdict.action !== SG.ACTION.BLOCK,
    });

    if (decision !== "proceed") {
      discard();
      return;
    }

    // Both branches are guarded, and the redaction branch is the one
    // that would have been worst. `sendRedacted` OVERWRITES the
    // composer with the server's replacement — a redaction computed
    // from the text as it was when analysed. Had the user edited in
    // the meantime, that write would have destroyed their edit and
    // sent a rewrite of a message that no longer existed.
    if (!(await replayIsStillValid(depth))) return;

    // The modal previews a TRUNCATED redaction for readability; what
    // gets sent is the full string from the server. Sending the
    // preview would silently drop the tail of long messages.
    if (redacting) await sendRedacted(verdict.redacted_text);
    else approve();
  }

  // ---------------------------------------------------------------
  // Image interception
  // ---------------------------------------------------------------
  const imageInterceptor = new SG.ImageInterceptor(adapter, handleImage);
  imageInterceptor.start();

  /**
   * Decide whether one attached image may go through.
   *
   * Runs at ATTACH time, not send time, and the difference is the
   * whole point: by send time ChatGPT has already uploaded the file to
   * its own servers and the composer holds only a remote reference.
   * The bytes would have left the machine before we ever looked.
   *
   * @param {File} file
   * @param {{trigger: string}} context
   * @returns {Promise<boolean>} true to let it through
   */
  async function handleImage(file, context) {
    if (modal.isOpen) {
      console.debug(`${TAG} a decision is already pending — dropping this image.`);
      return false;
    }

    // Checked here as well as on the server. Uploading eight megabytes
    // to be told 413 wastes the user's time on a request that could
    // only ever fail.
    if (file.size > SG.CONFIG.MAX_IMAGE_BYTES) {
      const decision = await modal.show({
        variant: "offline",
        message:
          `This image is ${(file.size / 1024 / 1024).toFixed(1)}MB, larger than ` +
          `ShadowGuard can inspect. It has NOT been checked for sensitive content.`,
        allowProceed: SG.CONFIG.FAIL_MODE === "open",
      });

      return decision === "proceed";
    }

    const started = performance.now();
    const result = await ask({
      type: SG.MSG.ANALYZE_IMAGE,
      payload: {
        image_b64: await toBase64(file),
        mime: file.type,
        platform: adapter.id,
        trigger: context.trigger,
        client_version: VERSION,
      },
    });

    // ---- Backend unreachable: fail open, but visibly -------------
    if (!result?.ok) {
      console.warn(`${TAG} could not analyse image — ${result?.error ?? "no response"}`);

      const decision = await modal.show({
        variant: "offline",
        message: result?.disconnected
          ? "ShadowGuard was reloaded while this page was open, so this image " +
            "has NOT been checked. Reload the page to restore protection."
          : "ShadowGuard could not check this image for sensitive content. " +
            "Attach it only if you are confident it is safe.",
        allowProceed: SG.CONFIG.FAIL_MODE === "open",
      });

      return decision === "proceed";
    }

    const verdict = result.verdict;

    console.log(
      `${TAG} image (${context.trigger}) → ${verdict.action.toUpperCase()} ` +
      `(score ${verdict.score}, read ${verdict.ocr?.words ?? 0} words at ` +
      `${Math.round((verdict.ocr?.confidence ?? 0) * 100)}% confidence, ` +
      `${Math.round(performance.now() - started)}ms, id ${verdict.request_id})`
    );

    if (verdict.action === SG.ACTION.ALLOW) return true;

    const decision = await modal.show({
      level: verdict.level,
      score: verdict.score,
      entities: [...new Set(verdict.entities.map((e) => e.type))],
      message: verdict.message,

      // What was READ, never what it said. The modal is on a page we
      // do not own, and putting the extracted text on screen would
      // display the secret we just found — the same mistake as logging
      // it, with an audience.
      preview:
        `image · ${verdict.ocr?.width}×${verdict.ocr?.height} · ` +
        `${verdict.ocr?.words ?? 0} words read at ` +
        `${Math.round((verdict.ocr?.confidence ?? 0) * 100)}% confidence`,

      allowProceed: verdict.action !== SG.ACTION.BLOCK,
    });

    return decision === "proceed";
  }

  /**
   * A File as base64, without the data-URL prefix.
   *
   * `chrome.runtime.sendMessage` serialises through JSON, so a Blob
   * does not survive the hop to the service worker — it arrives as an
   * empty object, with no error anywhere.
   */
  function toBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(reader.error);
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
      reader.readAsDataURL(file);
    });
  }

  /**
   * Ask the service worker something, without ever throwing.
   *
   * `chrome.runtime.sendMessage` REJECTS — it does not resolve with an
   * error — when the messaging channel is gone. The two ways that
   * happens are both routine:
   *
   *   - the extension was reloaded, updated or disabled while this
   *     page was already open ("Extension context invalidated")
   *   - the service worker failed to start at all
   *
   * The first is what happens every single time you press ↻ on
   * chrome://extensions without also reloading the tab, which is the
   * most common thing a developer does.
   *
   * WHY THIS WRAPPER EXISTS AT ALL. The caller already handles a
   * FAILED analysis by failing open with a visible notice (D12). But a
   * REJECTED promise skipped that path entirely: the exception escaped
   * `handleIntercept`, the message stayed cancelled, and nothing was
   * ever replayed. The composer simply stopped working, with only a
   * console error to say why.
   *
   * That is failing CLOSED, silently — the precise opposite of the
   * stated policy, arrived at by forgetting that a rejection is not a
   * return value. Converting the throw into `{ok: false}` puts this
   * failure back onto the path that was designed for it.
   */
  async function ask(message) {
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (error) {
      const invalidated = /context invalidated|receiving end does not exist/i.test(
        error?.message ?? ""
      );

      if (invalidated) {
        // The extension is gone from under this page. Nothing we send
        // will ever be answered, so stop cancelling sends — otherwise
        // every subsequent message is held for an analysis that can
        // never arrive, and the user cannot use ChatGPT at all until
        // they reload.
        interceptor.enabled = false;
        imageInterceptor.enabled = false;

        console.warn(
          `%c ${TAG} DISCONNECTED `,
          "background:#a11; color:#fff; padding:2px 6px; border-radius:3px;",
          "\n  The extension was reloaded or updated while this page was open, " +
          "so this tab can no longer reach it." +
          "\n  Interception is now OFF so the page stays usable." +
          "\n  RELOAD THE PAGE to restore protection."
        );
      }

      return { ok: false, error: error?.message ?? "no response", disconnected: invalidated };
    }
  }

  /** Trim the user's text to something that fits the modal. */
  function previewOf(text) {
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
  }

  // ---------------------------------------------------------------
  // Acting on the decision
  // ---------------------------------------------------------------
  /**
   * May the held send be replayed, or has the text moved on?
   *
   * This closes bypass **B1**, and the bug it closes is a
   * time-of-check-to-time-of-use gap, not a detection failure. The
   * text is inspected at one moment and sent at another, and the
   * composer is editable throughout the interval. So:
   *
   *   1. trip a warning with something harmless
   *   2. paste a secret while the dialog is open
   *   3. click Proceed
   *
   * and the secret is sent under an approval the user gave for
   * different text. Nothing in the pipeline ever saw it. No detector
   * improvement can close this, because detection was never the thing
   * that failed.
   *
   * What was here before was a `console.warn`. That is worth naming
   * precisely rather than glossing: the check was already written and
   * already correct, and it reported the violation to a place no user
   * looks. A control whose output nobody reads is not a mitigation, it
   * is a record of a decision not taken.
   *
   * The fix is to treat edited text as what it is — a new message
   * nobody has inspected — and analyse it. Not to send it with a
   * warning, and not to refuse outright either, because the common
   * case is innocent: the dialog says "there is a card number in
   * here", the user deletes it while the dialog is open and clicks
   * Proceed. Re-analysis handles that exactly right — the new text is
   * clean, it comes back `allow`, and it sends with no further
   * ceremony. Refusing would have made the correct action feel broken.
   *
   * @param {number} depth how many times this send has been re-analysed
   * @returns {Promise<boolean>} true if the caller may replay
   */
  async function replayIsStillValid(depth) {
    // No pending send or no composer: not this function's problem.
    // `approve()` reports both, and duplicating the message here would
    // produce two warnings for one fault.
    if (!pending || !composer) return true;

    const current = adapter.readText(composer);
    if (current === pending.text) return true;

    console.warn(
      `${TAG} composer changed after inspection ` +
      `(${pending.text.length} → ${current.length} chars) — re-analysing, not sending.`
    );

    // ---- The bound, and why it fails closed --------------------
    // Re-analysis is a loop with a human in it, and a hostile page is
    // not obliged to be human. Rewriting the composer on every dialog
    // close would spin this forever, one HTTP request per turn.
    //
    // On exhaustion we do NOT send. The alternative — give up
    // re-checking and replay whatever is there — would turn the bound
    // into the bypass, which is a familiar way for a rate limit to
    // become the hole it was protecting.
    if (depth >= MAX_RECHECKS) {
      console.error(`${TAG} text changed ${depth} times — refusing to send.`);
      discard();

      await modal.show({
        variant: "changed",
        message:
          "The message kept changing while ShadowGuard was checking it, so " +
          "nothing has been sent. Your text is still in the box — send it " +
          "again when you have finished editing.",
        allowProceed: false,
      });

      return false;
    }

    // Re-enter the ordinary path with the text that is actually there.
    // Clearing `pending` first matters: handleIntercept sets it, and
    // leaving the old value in place would let the guard at the top of
    // this function compare against a message that no longer exists.
    const trigger = pending.trigger;
    pending = null;

    await handleIntercept(current, { trigger }, depth + 1);

    // False regardless of what the recursive call decided. That call
    // owns the outcome now — it has already sent, or shown its own
    // dialog, or discarded. Returning true here would replay a second
    // time on top of it.
    return false;
  }

  /**
   * Send the held message.
   *
   * @param {{silent?: boolean}} [options]
   * @returns {boolean} whether a replay was attempted
   */
  function approve({ silent = false } = {}) {
    if (!pending) {
      console.warn(`${TAG} nothing pending to approve.`);
      return false;
    }
    if (!composer) {
      console.error(`${TAG} composer went away — cannot replay.`);
      pending = null;
      return false;
    }

    // Defence in depth, and the last line of it.
    //
    // `replayIsStillValid()` has already run at every call site, so in
    // normal operation this never fires. It stays because approve() is
    // reachable from the `__sg` debug handle and from any call site
    // added later, and the invariant it protects is the one the whole
    // interception exists to hold: THE BYTES SENT ARE THE BYTES THAT
    // WERE INSPECTED. A future caller that forgets the guard should
    // get a refused send, not a silent bypass.
    //
    // Refusing rather than warning is the change that closes B1. The
    // previous version logged and sent anyway, which made the console
    // the only witness to a policy violation.
    const current = adapter.readText(composer);
    if (current !== pending.text) {
      console.error(
        `${TAG} refusing to replay — composer text changed since inspection ` +
        `(${pending.text.length} → ${current.length} chars).`
      );
      pending = null;
      return false;
    }

    const result = adapter.send(composer);
    pending = null;

    if (!silent) {
      console.log(
        `%c ${TAG} SEND REPLAYED `,
        "background:#0a7d47; color:#fff; padding:2px 6px; border-radius:3px;",
        `via ${result.method} (${result.detail})`
      );
    }

    // ---- Verify the effect, do not assume the action worked ------
    // A send that silently no-ops is the worst outcome: the user
    // believes their approved message went, and it did not.
    //
    // Deliberately a warning and NOT an auto-retry. Retrying a failed
    // action is good practice in most apps; in a DLP tool it means the
    // message you were already unsure about gets sent twice.
    setTimeout(() => {
      if (composer && !adapter.isEmpty(composer)) {
        console.warn(
          `${TAG} composer still has text ${VERIFY_DELAY_MS}ms after replay — ` +
          `the send may not have gone through. Not retrying automatically.`
        );
      }
    }, VERIFY_DELAY_MS);

    return true;
  }

  /** Drop the held message. The text stays in the box so it can be edited. */
  function discard() {
    if (!pending) {
      console.warn(`${TAG} nothing pending to discard.`);
      return false;
    }
    pending = null;
    console.log(`${TAG} pending send discarded — text left in the composer for editing.`);
    return true;
  }

  /**
   * Replace the composer's contents, then send.
   *
   * Used for the `redact` decision, which is the only path where
   * ShadowGuard rewrites what the user typed — and it does so only
   * after they have seen the exact replacement and clicked approve.
   *
   * @param {string} replacement the redacted text from the backend
   * @returns {boolean}
   */
  async function sendRedacted(replacement) {
    if (!pending || !composer) {
      console.warn(`${TAG} nothing pending to redact.`);
      return false;
    }

    const original = pending.text;

    await adapter.replaceText(composer, replacement);

    // ---- Verify on a LATER FRAME, not immediately ---------------
    // The first version checked straight away and reported success on
    // a message that went out with the card number still in it.
    //
    // Two things were wrong with it. It read `innerText` — the same
    // layer it had just written to — which proves the DOM changed and
    // says nothing about whether the editor's MODEL changed. And
    // ProseMirror reconciles asynchronously: when it rejects a
    // mutation it re-renders the DOM from its model a frame later. So
    // an immediate read sees our own write; a read after a frame sees
    // what the editor decided to keep.
    //
    // Two frames rather than one, because the observer callback and
    // the re-render can land in different ticks.
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))
    );

    // ---- Check for the SECRET, not for the placeholder -----------
    // "Does it contain [REDACTED:?" is the wrong question — a partial
    // write can satisfy it while the original text is still present.
    // The only question that matters is whether what we were trying to
    // remove is gone.
    const actual = adapter.readText(composer);

    if (actual === original || actual.trim() !== replacement.trim()) {
      // Deliberately does NOT claim the composer holds the original.
      //
      // The first version of this message said exactly that, and it
      // was wrong: a partial edit can leave the box holding the
      // original AND fragments of the replacement, which is neither
      // state. Telling a user their text is intact when it is not is
      // worse than telling them nothing, because they will send it
      // without looking.
      console.error(
        `%c ${TAG} REDACTION FAILED — SEND ABANDONED `,
        "background:#a11; color:#fff; padding:2px 6px; border-radius:3px;",
        `\n  wanted : ${replacement}` +
        `\n  found  : ${actual}` +
        `\n\n  Nothing was sent. The composer may now hold a partial or ` +
        `duplicated edit — CHECK IT before sending anything.` +
        `\n  Run __sg.clear() to empty it.`
      );
      pending = null;
      return false;
    }

    pending = { ...pending, text: replacement };
    console.log(
      `%c ${TAG} SENDING REDACTED `,
      "background:#7d5a0a; color:#fff; padding:2px 6px; border-radius:3px;",
      replacement
    );

    return approve({ silent: true });
  }

  // ---------------------------------------------------------------
  // Manual debugging handle
  // ---------------------------------------------------------------
  // Lets you poke at the live state from the DevTools console.
  //
  // IMPORTANT: the console defaults to the PAGE's world, where this
  // does not exist. Use the context dropdown (top-left of the Console
  // panel, usually reading "top") and switch to "ShadowGuard AI".
  // Forgetting this step produces "__sg is not defined" on perfectly
  // working code, and costs beginners an hour.
  window.__sg = {
    adapter,
    watcher,
    interceptor,
    get composer() {
      return composer;
    },
    read: () => adapter.readText(composer),
    isEmpty: () => adapter.isEmpty(composer),

    /** Stop blocking Enter, so you can use ChatGPT normally. */
    disable() {
      interceptor.enabled = false;
      console.log(`${TAG} interception DISABLED — Enter now sends normally.`);
    },

    /** Resume blocking. */
    enable() {
      interceptor.enabled = true;
      console.log(`${TAG} interception ENABLED.`);
    },

    /** Guard-behaviour counters: {seen, intercepted, passed}. */
    stats: () => ({ ...interceptor.stats }),

    /** The currently held send, or null. Stands in for the modal's state. */
    get pending() {
      return pending;
    },

    /**
     * The dialog instance. Exposed so a test can drive the paths that
     * only exist behind it — B1's re-analysis is reached by clicking
     * Proceed, and a suite with no handle on the modal cannot get
     * there at all.
     */
    modal,

    /** Modal "Proceed" — replay the held send. */
    approve,

    /** Modal "Cancel" — drop it, leaving the text in the box. */
    discard,

    /** Modal "Send redacted" — swap the composer's text, then send. */
    sendRedacted,

    /** Empty the composer. For cleaning up after a failed rewrite. */
    async clear() {
      await adapter.replaceText(composer, "");
      return adapter.readText(composer);
    },

    /**
     * Diagnose the composer rewrite without sending anything.
     *
     * Reports the DOM twice: immediately after the write, and after
     * two animation frames. The gap between those two readings is the
     * whole diagnosis.
     *
     *   immediate ✓  settled ✓   the editor accepted it
     *   immediate ✓  settled ✗   the editor REVERTED it — the model
     *                            never changed, and the old version of
     *                            this helper reported success here
     *   immediate ✗              the write never landed at all
     */
    async tryReplace(text = "[REDACTED: TEST]") {
      const before = adapter.readText(composer);
      const write = await adapter.replaceText(composer, text);
      const immediate = adapter.readText(composer);

      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))
      );
      const settled = adapter.readText(composer);

      const result = {
        before,
        immediate,
        settled,
        // The number that diagnoses the append bug. If the selection
        // covers 0 characters, the editor inserts at a collapsed
        // cursor and the old text survives in front of the new.
        selectedChars: write.selected,
        path: write.path,
        replaced: settled.trim() === text.trim(),
        appended: settled.includes(before.trim()) && before.trim() !== "",
      };

      console.log(
        `${TAG} replace diagnosis — ` +
        `selection covered ${result.selectedChars} chars, via ${result.path}, ` +
        `${result.replaced ? "REPLACED correctly" : result.appended ? "APPENDED (bug)" : "unclear"}`,
        result
      );

      return result;
    },

    /** Where would send() route right now? Useful when replay misbehaves. */
    sendButton: () => adapter.findSendButton(),

    /** The image interceptor — paste and file-input paths. */
    imageInterceptor,

    /** The modal instance. Its shadow root is closed, so this is the only handle. */
    modal,

    /**
     * Render the modal with sample data, to check it visually without
     * needing an interception or a backend. Task 2.5 replaces this
     * with the real flow.
     *
     * @param {string} level  low | medium | high | critical
     */
    demoModal(level = "high") {
      const samples = {
        low: {
          score: 18, entities: ["EMAIL_ADDRESS"],
          message: "Minor personal data detected. Sending is allowed.",
          preview: "contact me at alex@example.com",
        },
        medium: {
          score: 47, entities: ["PERSON", "PHONE_NUMBER"],
          message: "This message contains personal information about a named individual.",
          preview: "Priya Raman, +44 7700 900123, account overdue",
        },
        high: {
          score: 78, entities: ["AWS_ACCESS_KEY", "EMAIL_ADDRESS"],
          message: "This message appears to contain a cloud credential and personal data.",
          preview: "AKIA••••••••••••7Q2M  ·  ops@acme-internal.com",
        },
        critical: {
          score: 96, entities: ["OPENAI_API_KEY", "PRIVATE_KEY"],
          message:
            "A live secret was detected. Sending has been blocked and the attempt logged.",
          preview: "sk-proj-••••••••••••••••••••••••••••3f9A",
          allowProceed: false,
        },
      };

      return modal
        .show({ level, ...samples[level] })
        .then((decision) => {
          console.log(`${TAG} demo modal decision:`, decision);
          return decision;
        });
    },
  };

  // ---------------------------------------------------------------
  // Bridge check (Task 1.4)
  // ---------------------------------------------------------------
  // In Week 3 this same call carries the prompt and the reply carries
  // the risk verdict — only the payload changes.
  chrome.runtime
    .sendMessage({
      type: SG.MSG.PING,
      payload: { host: location.hostname, sentAt: new Date().toISOString() },
    })
    .then((response) => {
      console.log(`${TAG} bridge OK — reply from service worker:`, response);
    })
    .catch((error) => {
      // Usually the service worker failed to load at all (a syntax
      // error in service_worker.js), leaving nothing listening.
      console.error(`${TAG} bridge FAILED:`, error.message);
    });
})();
