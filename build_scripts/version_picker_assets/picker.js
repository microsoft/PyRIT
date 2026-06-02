/* PyRIT version picker
 *
 * Vanilla JS, no framework dependencies. Inlined into every doc page's <head>
 * by build_scripts/inject_version_picker.py.
 *
 * Fetches /<repo-base>/versions.json (computed from current location) and renders
 * a floating bottom-right dropdown. Selecting a version navigates to the same
 * relative path in that version.
 *
 * Design constraints (verified by browser inspection):
 *
 *   The myst-cli/Remix theme that PyRIT uses aggressively reconciles the body
 *   and head during hydration / route transitions, removing any <script>,
 *   <meta>, <link>, or <style> tags it didn't render server-side. This is the
 *   same upstream bug that breaks the RTD addons flyout.
 *
 *   Defenses:
 *     1. The script tag is inline in <head> so the JS runs during HTML parse,
 *        BEFORE React hydrates. By the time React removes the <script>, our
 *        observers are live.
 *     2. The CSS is bundled into this file as a constant string and injected
 *        via a JS-created <style>. We do NOT use a <link rel="stylesheet">:
 *        React removes those during hydration and removes their styles too.
 *     3. We re-mount the picker AND re-inject the styles when React wipes them.
 */
(function () {
  "use strict";

  var MOUNT_CLASS = "pyrit-version-picker";
  var STYLE_ID = "pyrit-version-picker-style";

  var CSS = [
    ".pyrit-version-picker {",
    "  position: fixed;",
    "  bottom: 1rem;",
    "  right: 1rem;",
    "  z-index: 2147483600;",
    "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;",
    "  font-size: 0.875rem;",
    "  line-height: 1.2;",
    "}",
    ".pyrit-version-picker__button {",
    "  display: inline-flex;",
    "  align-items: center;",
    "  gap: 0.4rem;",
    "  padding: 0.5rem 0.75rem;",
    "  background: #1e293b;",
    "  color: #f8fafc;",
    "  border: 1px solid #334155;",
    "  border-radius: 0.5rem;",
    "  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);",
    "  cursor: pointer;",
    "  font: inherit;",
    "  transition: background-color 0.15s ease;",
    "}",
    ".pyrit-version-picker__button:hover,",
    ".pyrit-version-picker__button:focus-visible {",
    "  background: #334155;",
    "  outline: 2px solid #60a5fa;",
    "  outline-offset: 1px;",
    "}",
    ".pyrit-version-picker__icon {",
    "  display: inline-flex;",
    "  align-items: center;",
    "  justify-content: center;",
    "  width: 1.25rem;",
    "  height: 1.25rem;",
    "  border-radius: 50%;",
    "  background: #60a5fa;",
    "  color: #0f172a;",
    "  font-weight: 700;",
    "  font-size: 0.75rem;",
    "}",
    ".pyrit-version-picker__label { font-weight: 500; }",
    ".pyrit-version-picker__caret { font-size: 0.625rem; opacity: 0.7; }",
    ".pyrit-version-picker__menu {",
    "  position: absolute;",
    "  bottom: calc(100% + 0.5rem);",
    "  right: 0;",
    "  min-width: 14rem;",
    "  margin: 0;",
    "  padding: 0.25rem 0;",
    "  list-style: none;",
    "  background: #ffffff;",
    "  color: #0f172a;",
    "  border: 1px solid #cbd5e1;",
    "  border-radius: 0.5rem;",
    "  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);",
    "  max-height: 60vh;",
    "  overflow-y: auto;",
    "}",
    ".pyrit-version-picker__item { margin: 0; }",
    ".pyrit-version-picker__link {",
    "  display: flex;",
    "  align-items: baseline;",
    "  justify-content: space-between;",
    "  gap: 0.5rem;",
    "  padding: 0.5rem 0.875rem;",
    "  color: inherit;",
    "  text-decoration: none;",
    "}",
    ".pyrit-version-picker__link-label { flex: 1; }",
    ".pyrit-version-picker__link-suffix {",
    "  font-size: 0.75rem;",
    "  opacity: 0.7;",
    "  font-weight: 400;",
    "}",
    ".pyrit-version-picker__link-suffix--fallback { color: #b45309; opacity: 1; }",
    ".pyrit-version-picker__link:hover,",
    ".pyrit-version-picker__link:focus-visible { background: #f1f5f9; outline: none; }",
    ".pyrit-version-picker__item--current .pyrit-version-picker__link {",
    "  background: #dbeafe;",
    "  color: #1d4ed8;",
    "  font-weight: 600;",
    "}",
    "@media (prefers-color-scheme: dark) {",
    "  .pyrit-version-picker__menu { background: #0f172a; color: #f8fafc; border-color: #334155; }",
    "  .pyrit-version-picker__link:hover, .pyrit-version-picker__link:focus-visible { background: #1e293b; }",
    "  .pyrit-version-picker__item--current .pyrit-version-picker__link { background: #1e3a8a; color: #bfdbfe; }",
    "}",
    "@media print { .pyrit-version-picker { display: none; } }"
  ].join("\n");

  var manifestCache = null;
  var manifestPromise = null;
  var pagesCache = {}; // versionBase -> Promise<string[] | null>
  var lastPath = window.location.pathname;
  var mountInProgress = false;

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var head = document.head || document.getElementsByTagName("head")[0];
    if (!head) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    head.appendChild(style);
  }

  function computeSiteBase() {
    var meta = document.querySelector('meta[name="pyrit-docs-base"]');
    if (meta && meta.content) return meta.content.replace(/\/$/, "");
    var parts = window.location.pathname.split("/").filter(Boolean);
    if (parts.length === 0) return "";
    return "/" + parts[0];
  }

  function computeCurrentSlug(base) {
    var path = window.location.pathname;
    if (!path.startsWith(base + "/")) return null;
    var rest = path.slice(base.length + 1);
    var slash = rest.indexOf("/");
    var slug = slash === -1 ? rest : rest.slice(0, slash);
    return slug || null;
  }

  function computeRelativePath(base, slug) {
    if (!slug) return "";
    var prefix = base + "/" + slug + "/";
    var path = window.location.pathname;
    if (!path.startsWith(prefix)) return "";
    return path.slice(prefix.length) + window.location.search + window.location.hash;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      }
    }
    if (children) children.forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function buildPicker(manifest, base, currentSlug, relPath) {
    var container = el("div", { class: MOUNT_CLASS, role: "navigation", "aria-label": "Documentation version" });
    container.dataset.pyritPicker = "1";
    var button = el("button", { class: "pyrit-version-picker__button", type: "button", "aria-haspopup": "listbox", "aria-expanded": "false" });
    var currentEntry = manifest.versions.find(function (v) { return v.slug === currentSlug; });
    var currentLabel = currentEntry ? currentEntry.name : (currentSlug || "version");
    var stableBadge = (currentEntry && currentEntry.slug === manifest.stable) ? " (stable)" : "";
    button.innerHTML = '<span class="pyrit-version-picker__icon" aria-hidden="true">v</span> <span class="pyrit-version-picker__label">' + escapeHtml(currentLabel + stableBadge) + '</span> <span class="pyrit-version-picker__caret" aria-hidden="true">\u25BE</span>';

    var menu = el("ul", { class: "pyrit-version-picker__menu", role: "listbox", hidden: "" });
    var linksToProbe = []; // [{slug, link, suffix, base, ancestors}]
    manifest.versions.forEach(function (v) {
      var isCurrent = v.slug === currentSlug;
      var label = v.name + (v.slug === manifest.stable ? " (stable)" : "");
      var versionBase = base + "/" + v.slug + "/";
      var fullHref = versionBase + (relPath || "");
      var item = el("li", { class: "pyrit-version-picker__item" + (isCurrent ? " pyrit-version-picker__item--current" : ""), role: "option", "aria-selected": isCurrent ? "true" : "false" });
      var link = el("a", { href: fullHref, class: "pyrit-version-picker__link" });
      var labelSpan = el("span", { class: "pyrit-version-picker__link-label", text: label });
      var suffix = el("span", { class: "pyrit-version-picker__link-suffix", "aria-hidden": "true" });
      link.appendChild(labelSpan);
      link.appendChild(suffix);
      item.appendChild(link);
      menu.appendChild(item);
      // Queue an ancestor-walk probe for non-current versions when there's
      // a non-trivial path. Each ancestor (most specific first, down to root)
      // is probed in parallel; the deepest one that exists wins.
      if (!isCurrent && relPath) {
        linksToProbe.push({ slug: v.slug, link: link, suffix: suffix, versionBase: versionBase });
      }
    });
    // Probe page availability per non-current version using each version's
    // pages.json manifest, updating links to the closest matching page.
    linksToProbe.forEach(function (p) { probeAndUpdateLink(p, relPath); });

    function toggle(open) {
      var isOpen = open === undefined ? menu.hidden : !open;
      if (isOpen) { menu.hidden = false; button.setAttribute("aria-expanded", "true"); }
      else        { menu.hidden = true;  button.setAttribute("aria-expanded", "false"); }
    }

    button.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    document.addEventListener("click", function (e) { if (!container.contains(e.target)) toggle(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") toggle(false); });

    container.appendChild(button);
    container.appendChild(menu);
    return container;
  }

  // Fetch a version's pages.json (cached). Returns null on any error so
  // the picker can degrade gracefully (full-path link, no fallback).
  function getPages(versionBase) {
    if (pagesCache[versionBase]) return pagesCache[versionBase];
    pagesCache[versionBase] = fetch(versionBase + "pages.json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("pages.json HTTP " + r.status);
        return r.json();
      })
      .then(function (pages) { return Array.isArray(pages) ? pages : null; })
      .catch(function () { return null; });
    return pagesCache[versionBase];
  }

  // findClosestPage + commonSegmentPrefix come from closest_page.js, the
  // single source of truth shared with the 404 page's auto-redirect script
  // (see compose_docs_dist.py.render_not_found_html). inject_version_picker.py
  // substitutes the marker below with the contents of closest_page.js at
  // build time. Tests in test_closest_page.py exercise the algorithm against
  // the real JS file via node so the two consumers can't drift in behavior.
  // @@CLOSEST_PAGE_JS@@

  // For one non-current version: load the manifest, find the closest page,
  // and update the link href + suffix. If the manifest can't be loaded (e.g.
  // the version was built without it), fall back to leaving the full-path
  // link in place.
  function probeAndUpdateLink(p, relPath) {
    getPages(p.versionBase).then(function (pages) {
      if (!pages) return; // no manifest -- leave full-path link as-is
      var match = findClosestPage(pages, relPath);
      if (match === null) return;
      var clean = String(relPath || "").split("?")[0].split("#")[0].replace(/\/+$/, "");
      var fullTarget = clean ? clean + "/" : "";
      if (match === fullTarget) return; // exact match; no suffix needed

      var href = p.versionBase + match;
      p.link.setAttribute("href", href);
      if (match === "") {
        p.suffix.textContent = " \u2192 home";
        p.link.title = "This page doesn't exist in this version; opens the version's home page instead.";
      } else {
        p.suffix.textContent = " \u2192 /" + match.replace(/\/$/, "");
        p.link.title = "This page doesn't exist in this version; opens the closest available page (" + match + ") instead.";
      }
      p.suffix.className += " pyrit-version-picker__link-suffix--fallback";
    });
  }

  function getManifest(base) {
    if (manifestCache) return Promise.resolve(manifestCache);
    if (manifestPromise) return manifestPromise;
    manifestPromise = fetch(base + "/versions.json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("manifest HTTP " + r.status);
        return r.json();
      })
      .then(function (m) { manifestCache = m; return m; });
    return manifestPromise;
  }

  function mountPicker() {
    if (mountInProgress) return;
    if (document.querySelector("." + MOUNT_CLASS)) return;
    if (!document.body) return;
    mountInProgress = true;
    ensureStyles();
    var base = computeSiteBase();
    var currentSlug = computeCurrentSlug(base);
    var relPath = computeRelativePath(base, currentSlug);
    getManifest(base)
      .then(function (manifest) {
        if (!manifest || !Array.isArray(manifest.versions) || manifest.versions.length === 0) {
          mountInProgress = false; return;
        }
        if (document.querySelector("." + MOUNT_CLASS)) { mountInProgress = false; return; }
        ensureStyles();
        var node = buildPicker(manifest, base, currentSlug, relPath);
        document.body.appendChild(node);
        mountInProgress = false;
      })
      .catch(function (err) {
        mountInProgress = false;
        if (window.console) console.warn("[pyrit-version-picker]", err);
      });
  }

  // Wait for the DOM to be quiescent (no mutations for `quietMs`) before mounting.
  function mountWhenQuiet(quietMs) {
    quietMs = quietMs || 250;
    var timer = null;
    var observer = new MutationObserver(function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(done, quietMs);
    });
    function done() { observer.disconnect(); mountPicker(); }
    observer.observe(document.body, { childList: true, subtree: true });
    timer = setTimeout(done, quietMs);
    setTimeout(function () {
      if (timer) { observer.disconnect(); clearTimeout(timer); mountPicker(); }
    }, 5000);
  }

  // After picker is mounted, watch for React reconciliations that remove us.
  function watchForRemoval() {
    var observer = new MutationObserver(function () {
      if (window.location.pathname !== lastPath) {
        lastPath = window.location.pathname;
        var existing = document.querySelector("." + MOUNT_CLASS);
        if (existing) existing.remove();
        mountWhenQuiet(150);
      } else if (!document.querySelector("." + MOUNT_CLASS)) {
        mountWhenQuiet(150);
      } else if (!document.getElementById(STYLE_ID)) {
        ensureStyles();
      }
    });
    observer.observe(document.body, { childList: true, subtree: false });
    // Also watch head for our style being removed by React.
    if (document.head) {
      var headObserver = new MutationObserver(function () {
        if (!document.getElementById(STYLE_ID)) ensureStyles();
      });
      headObserver.observe(document.head, { childList: true });
    }
  }

  function hookHistory() {
    var origPush = history.pushState;
    var origReplace = history.replaceState;
    history.pushState = function () {
      var r = origPush.apply(this, arguments);
      window.dispatchEvent(new Event("pyrit:navigated"));
      return r;
    };
    history.replaceState = function () {
      var r = origReplace.apply(this, arguments);
      window.dispatchEvent(new Event("pyrit:navigated"));
      return r;
    };
    window.addEventListener("popstate", function () {
      window.dispatchEvent(new Event("pyrit:navigated"));
    });
    window.addEventListener("pyrit:navigated", function () {
      lastPath = window.location.pathname;
      var existing = document.querySelector("." + MOUNT_CLASS);
      if (existing) existing.remove();
      mountWhenQuiet(150);
    });
  }

  function start() {
    hookHistory();
    mountWhenQuiet();
    setTimeout(watchForRemoval, 1500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
