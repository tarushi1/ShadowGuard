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
 * @param {function} adapter.readText    (element) => string
 * @param {function} adapter.isEmpty     (element) => boolean
 */
globalThis.SG.registerAdapter = function registerAdapter(adapter) {
  const required = ["id", "label", "hosts", "selectors", "readText", "isEmpty"];
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
