/**
 * A contenteditable that behaves the way ProseMirror was MEASURED to.
 * ================================================================
 *
 * The composer rewrite took four attempts (D39). Every wrong version
 * looked correct against a mental model of how a rich-text editor
 * "should" work. This file encodes the model that actually matched the
 * observations, so those four failures are reproducible in a test
 * instead of only in a browser.
 *
 * Three behaviours, each responsible for one of the failures:
 *
 * 1. THE MODEL IS THE TRUTH, THE DOM IS A RENDERING.
 *    The editor holds its own text. The DOM shows it. Writing to the
 *    DOM directly does not change what the editor believes, and the
 *    editor re-renders over it on the next frame.
 *    → caught: verification that read `innerText` straight after
 *      writing to it, and agreed with itself.
 *
 * 2. PASTE IS THE INPUT PATH, AND IT USES THE EDITOR'S SELECTION.
 *    A paste replaces whatever the editor believes is selected. The
 *    editor learns about selection from `selectionchange`, which is
 *    ASYNCHRONOUS — so a paste dispatched in the same tick as a
 *    selection change lands on the stale, collapsed selection.
 *    → caught: the version that appended a copy per attempt.
 *
 * 3. A COLLAPSED SELECTION MEANS INSERT, NOT REPLACE.
 *    Which is why an empty composer "worked" throughout: inserting at
 *    a cursor in an empty box is indistinguishable from replacing.
 *    → caught: tests that only ever started from an empty composer.
 */

/**
 * Attach fake-editor behaviour to an element.
 *
 * @param {Window}      window
 * @param {HTMLElement} element  a contenteditable
 * @param {string}      initial  starting text
 * @param {object}      [options]
 * @param {boolean}     [options.syncSelection=true]
 *        Whether the editor ever learns about selection changes. Set
 *        false to model an editor our nudge cannot reach — the state
 *        every failing version was actually in.
 * @param {boolean}     [options.acceptExecCommand=false]
 *        Whether `execCommand("insertText")` reaches the model.
 */
function attachFakeEditor(window, element, initial = "", options = {}) {
  const { syncSelection = true, acceptExecCommand = false } = options;

  const editor = {
    model: initial,
    /** What the editor believes is selected: [start, end] into `model`. */
    selection: [initial.length, initial.length],
    pastes: 0,
    render() {
      element.textContent = this.model;
    },
  };

  editor.render();

  // ---- Behaviour 2: selection is learned asynchronously -----------
  // The editor does not read the DOM selection when handling input; it
  // caches what `selectionchange` last told it. That single fact is
  // what turned four "obviously correct" fixes into appends.
  if (syncSelection) {
    window.document.addEventListener("selectionchange", () => {
      const selection = window.getSelection();
      const text = String(selection ?? "");

      editor.selection = text.length > 0 ? [0, editor.model.length] : [editor.model.length, editor.model.length];
    });
  }

  const replaceSelection = (text) => {
    const [start, end] = editor.selection;
    editor.model = editor.model.slice(0, start) + text + editor.model.slice(end);
    editor.selection = [start + text.length, start + text.length];
    editor.render();
  };

  // ---- The paste path ---------------------------------------------
  element.addEventListener("paste", (event) => {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    if (!text && text !== "") return;

    editor.pastes += 1;
    event.preventDefault(); // signals "handled", exactly as ProseMirror does
    replaceSelection(text);
  });

  if (acceptExecCommand) {
    window.document.execCommand = (command, _ui, value) => {
      if (command === "insertText") {
        replaceSelection(value ?? "");
        return true;
      }
      if (command === "selectAll") {
        editor.selection = [0, editor.model.length];
        return true;
      }
      return false;
    };
  }

  // ---- Behaviour 1: the editor re-renders over direct DOM writes ---
  // Polled rather than driven by MutationObserver so it stays simple
  // and deterministic under the test runner. The effect is what
  // matters: a write that did not go through an input path is gone by
  // the next frame.
  //
  // BOUNDED, and the bound is not cosmetic. An unbounded
  // `setTimeout(reconcile, 0)` chain keeps JSDOM's timer queue alive
  // forever, which keeps Node's event loop alive forever — the test
  // run hangs after every assertion has already passed. Loops that
  // reschedule themselves are the usual cause of a suite that "never
  // finishes" despite reporting no failures.
  let ticksRemaining = 60;
  const reconcile = () => {
    if (element.textContent !== editor.model) editor.render();
    if (ticksRemaining-- > 0) window.setTimeout(reconcile, 0);
  };
  window.setTimeout(reconcile, 0);

  editor.stop = () => {
    ticksRemaining = 0;
  };

  return editor;
}

module.exports = { attachFakeEditor };
