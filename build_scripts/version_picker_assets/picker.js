/* PyRIT version picker
 *
 * Vanilla JS, no framework dependencies. Injected into every doc page in every
 * version by build_scripts/inject_version_picker.py.
 *
 * Fetches /<repo-base>/versions.json (computed from current location) and renders
 * a floating bottom-right dropdown. Selecting a version navigates to the same
 * relative path in that version, falling back to that version's root if the path
 * doesn't exist.
 */
(function () {
  "use strict";

  // Find the site root: everything before the first version slug.
  // URLs look like /PyRIT/<slug>/... or /PyRIT/ for the root redirect.
  function computeSiteBase() {
    var path = window.location.pathname;
    // The injector writes <meta name="pyrit-docs-base" content="/PyRIT"> at build time.
    var meta = document.querySelector('meta[name="pyrit-docs-base"]');
    if (meta && meta.content) return meta.content.replace(/\/$/, "");
    // Fallback: best-effort. Strip the trailing filename and assume one-level base.
    var parts = path.split("/").filter(Boolean);
    if (parts.length === 0) return "";
    return "/" + parts[0];
  }

  // The version slug for *this* page, derived from URL.
  function computeCurrentSlug(base) {
    var path = window.location.pathname;
    if (!path.startsWith(base + "/")) return null;
    var rest = path.slice(base.length + 1);
    var slash = rest.indexOf("/");
    var slug = slash === -1 ? rest : rest.slice(0, slash);
    return slug || null;
  }

  // The "path within the version" part -- i.e. the part after /<base>/<slug>/.
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

  function render(manifest, base, currentSlug, relPath) {
    var container = el("div", { class: "pyrit-version-picker", role: "navigation", "aria-label": "Documentation version" });
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

  function mount() {
    var base = computeSiteBase();
    var currentSlug = computeCurrentSlug(base);
    var relPath = computeRelativePath(base, currentSlug);
    var manifestUrl = base + "/versions.json";

    fetch(manifestUrl, { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("manifest HTTP " + r.status);
        return r.json();
      })
      .then(function (manifest) {
        if (!manifest || !Array.isArray(manifest.versions) || manifest.versions.length === 0) return;
        var node = render(manifest, base, currentSlug, relPath);
        document.body.appendChild(node);
      })
      .catch(function (err) {
        // Silently fail. The site is still usable without the picker.
        if (window.console) console.warn("[pyrit-version-picker]", err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
