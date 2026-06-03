/* PyRIT closest-page algorithm -- single source of truth.
 *
 * Used by two consumers, both via build-time concatenation:
 *
 *   1. picker.js (the floating version picker dropdown). For each non-current
 *      version, the picker fetches that version's pages.json and uses
 *      findClosestPage(pages, relPath) to decide where to point the link --
 *      exact match, ancestor section, best sibling, or version home.
 *
 *   2. 404.html (built by compose_docs_dist.py). When the user lands on a
 *      URL that doesn't exist in the requested version, the inline script
 *      fetches pages.json and uses findClosestPage to auto-redirect.
 *
 * Both consumers concat this file into their own IIFE at build time. The
 * literal marker string is defined as a constant on the Python side: see
 * inject_version_picker.CLOSEST_PAGE_MARKER (consumed by _load_picker_js)
 * and compose_docs_dist.CLOSEST_PAGE_MARKER (consumed by
 * render_not_found_html). Each one replaces its marker in the host file
 * with the entire contents of this file.
 *
 * Tests load this file directly in node (tests/unit/build_scripts/
 * test_closest_page.py) and exercise findClosestPage() against fixtures so
 * we catch behavioral regressions in the real code, not a Python port.
 *
 * Conventions:
 *   - pages is the array from pages.json: every entry is a relative URL
 *     ending in "/" (e.g. "code/scenarios/scenarios/"), or "" for root.
 *   - relPath is the URL path the user is on, minus the docs base and
 *     version slug. Trailing slashes, query strings, and fragments are
 *     normalized off.
 *
 * Return values from findClosestPage:
 *   - a member of pages if any reasonable match exists
 *   - "" (the empty string) if only the version root is a fit
 *   - null if pages is empty or not an array (caller should fall back)
 */

function commonSegmentPrefix(pagePath, targetSegs) {
  var pageSegs = String(pagePath || "").replace(/\/+$/, "").split("/").filter(Boolean);
  var n = Math.min(pageSegs.length, targetSegs.length);
  var i = 0;
  while (i < n && pageSegs[i] === targetSegs[i]) i++;
  return i;
}

function findClosestPage(pages, relPath) {
  if (!Array.isArray(pages) || pages.length === 0) return null;
  var clean = String(relPath || "").split("?")[0].split("#")[0].replace(/\/+$/, "");
  var targetSegs = clean.split("/").filter(Boolean);

  // 1) exact match
  var full = clean ? clean + "/" : "";
  if (pages.indexOf(full) !== -1) return full;

  // 2) walk up ancestors, looking for siblings/descendants under each
  for (var depth = targetSegs.length - 1; depth >= 0; depth--) {
    var ancestor = depth === 0 ? "" : targetSegs.slice(0, depth).join("/") + "/";
    // ancestor itself a page?
    if (pages.indexOf(ancestor) !== -1) return ancestor;
    // any page under this ancestor? pick longest common prefix, tiebreak alphabetical
    var bestPage = null;
    var bestPrefix = -1;
    for (var i = 0; i < pages.length; i++) {
      var p = pages[i];
      if (ancestor !== "" && p.indexOf(ancestor) !== 0) continue;
      if (p === ancestor) continue;
      var pfx = commonSegmentPrefix(p, targetSegs);
      if (pfx > bestPrefix || (pfx === bestPrefix && bestPage && p < bestPage)) {
        bestPage = p;
        bestPrefix = pfx;
      }
    }
    if (bestPage) return bestPage;
  }

  // 3) root
  return "";
}
