/* PyRIT version picker
 *
 * Vanilla JS, no framework dependencies. Injected into every doc page in every
 * version by build_scripts/inject_version_picker.py.
 *
 * Fetches /<repo-base>/versions.json (computed from current location) and renders
 * a floating bottom-right dropdown. Selecting a version navigates to the same
 * relative path in that version, falling back to that version's root if the path
 * doesn't exist.
 *
 * The myst-cli/Remix theme that PyRIT uses re-hydrates the body during route
 * transitions and wipes anything React doesn't expect (verified: that's also
 * why the RTD addons flyout never appeared). To survive, we:
 *
 *   1. Cache the versions.json fetch so re-mounts are cheap
 *   2. Wait for React to finish hydrating before mounting (MutationObserver
 *      with quiescence detection on document.body)
 *   3. Re-mount on any SPA navigation (history.pushState / popstate)
 *   4. Re-mount if React removes us anyway (MutationObserver watching for
 *      our own removal)
 */
(function () {
  "use strict";

  var MOUNT_CLASS = "pyrit-version-picker";
  var manifestCache = null;
  var manifestPromise = null;
  var lastPath = window.location.pathname;
  var mountInProgress = false;

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
    manifest.versions.forEach(function (v) {
      var isCurrent = v.slug === currentSlug;
      var label = v.name + (v.slug === manifest.stable ? " (stable)" : "");
      var href = base + "/" + v.slug + "/" + (relPath || "");
      var item = el("li", { class: "pyrit-version-picker__item" + (isCurrent ? " pyrit-version-picker__item--current" : ""), role: "option", "aria-selected": isCurrent ? "true" : "false" });
      var link = el("a", { href: href, class: "pyrit-version-picker__link" });
      link.textContent = label;
      item.appendChild(link);
      menu.appendChild(item);
    });

    function toggle(open) {
      var isOpen = open === undefined ? menu.hidden : !open;
      if (isOpen) {
        menu.hidden = false;
        button.setAttribute("aria-expanded", "true");
      } else {
        menu.hidden = true;
        button.setAttribute("aria-expanded", "false");
      }
    }

    button.addEventListener("click", function (e) {
      e.stopPropagation();
      toggle();
    });
    document.addEventListener("click", function (e) {
      if (!container.contains(e.target)) toggle(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") toggle(false);
    });

    container.appendChild(button);
    container.appendChild(menu);
    return container;
  }

  function getManifest(base) {
    if (manifestCache) return Promise.resolve(manifestCache);
    if (manifestPromise) return manifestPromise;
    manifestPromise = fetch(base + "/versions.json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("manifest HTTP " + r.status);
        return r.json();
      })
      .then(function (m) {
        manifestCache = m;
        return m;
      });
    return manifestPromise;
  }

  function mountPicker() {
    if (mountInProgress) return;
    if (document.querySelector("." + MOUNT_CLASS)) return; // already mounted
    if (!document.body) return; // too early
    mountInProgress = true;

    var base = computeSiteBase();
    var currentSlug = computeCurrentSlug(base);
    var relPath = computeRelativePath(base, currentSlug);

    getManifest(base)
      .then(function (manifest) {
        if (!manifest || !Array.isArray(manifest.versions) || manifest.versions.length === 0) {
          mountInProgress = false;
          return;
        }
        if (document.querySelector("." + MOUNT_CLASS)) {
          mountInProgress = false;
          return;
        }
        var node = buildPicker(manifest, base, currentSlug, relPath);
        document.body.appendChild(node);
        mountInProgress = false;
      })
      .catch(function (err) {
        mountInProgress = false;
        if (window.console) console.warn("[pyrit-version-picker]", err);
      });
  }

  // Wait for the DOM to be quiescent (no mutations for `quietMs` ms) before
  // mounting. This avoids racing React's hydration which would wipe us.
  function mountWhenQuiet(quietMs) {
    quietMs = quietMs || 250;
    var timer = null;
    var observer = new MutationObserver(function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(done, quietMs);
    });
    function done() {
      observer.disconnect();
      mountPicker();
    }
    observer.observe(document.body, { childList: true, subtree: true });
    // Fallback in case the page never settles
    timer = setTimeout(done, quietMs);
    // Safety cap: try at most 5s
    setTimeout(function () {
      if (timer) {
        observer.disconnect();
        clearTimeout(timer);
        mountPicker();
      }
    }, 5000);
  }

  // After the picker is in the DOM, watch for React reconciliations that
  // accidentally remove us, and re-mount when that happens.
  function watchForRemoval() {
    var observer = new MutationObserver(function () {
      if (window.location.pathname !== lastPath) {
        lastPath = window.location.pathname;
        // Path changed: re-mount because relative path / current slug differ.
        var existing = document.querySelector("." + MOUNT_CLASS);
        if (existing) existing.remove();
        mountWhenQuiet(150);
      } else if (!document.querySelector("." + MOUNT_CLASS)) {
        // Picker got nuked but path hasn't changed: just re-mount.
        mountWhenQuiet(150);
      }
    });
    observer.observe(document.body, { childList: true, subtree: false });
  }

  // Hook history changes from SPA frameworks so we re-mount on route changes.
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
