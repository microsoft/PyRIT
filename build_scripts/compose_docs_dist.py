# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Compose the final docs deploy tree (``dist/``) from per-version build artifacts.

The docs workflow's deploy job uses this script to:

  1. Stage each ``artifacts/site-<slug>/`` directory into ``dist/<slug>/``.
  2. Generate a ``pages.json`` manifest of every rendered HTML page within
     each version (so the version picker can find sibling pages when the
     destination version is missing the exact URL the user was on).
  3. Write the top-level ``dist/versions.json`` manifest (parsed from the
     same source-of-truth ``docs-versions.yml`` rather than echoed from a
     shell variable, which would be fragile to YAML formatting changes).
  4. Write the root ``dist/index.html`` and ``dist/stable/index.html``
     meta-refresh redirects that point at the default / stable versions.
  5. Write a ``dist/404.html`` page that auto-redirects to the closest
     available sibling page within the same version (via the manifest).

Keeping all of this in a single Python script (instead of inline bash +
heredoc HTML templates in the workflow YAML) means each piece can be
linted, type-checked, and unit-tested. Adding a new redirect target or
tweaking the 404 markup is a code change, not a shell-quoting puzzle.

Usage:
    python build_scripts/compose_docs_dist.py \\
        --artifacts-dir artifacts \\
        --dist-dir dist \\
        --config .github/docs-versions.yml \\
        --base /PyRIT
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml
from generate_pages_manifest import collect_pages

# Single source of truth for the closest-page algorithm. Both the version
# picker (inject_version_picker.py) and the 404 page below concat the
# contents of closest_page.js into their own IIFE at build time, so the
# findClosestPage/commonSegmentPrefix functions can never drift between the
# two consumers. The marker string MUST match the one in
# inject_version_picker.CLOSEST_PAGE_MARKER.
CLOSEST_PAGE_MARKER = "// @@CLOSEST_PAGE_JS@@"
CLOSEST_PAGE_JS_PATH = Path(__file__).resolve().parent / "version_picker_assets" / "closest_page.js"

REDIRECT_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta http-equiv="refresh" content="0; url={target}">
  <link rel="canonical" href="{target}">
</head>
<body>
  <p>Redirecting to <a href="{target}">{link_text}</a>.</p>
</body>
</html>
"""

# The 404 page is rendered server-side; on load, the inlined script below
# fetches the requesting version's pages.json and redirects to the closest
# matching page if one exists. This static body shows only when the manifest
# is unavailable or no sibling exists in the version at all.
#
# The closest-page algorithm (findClosestPage + commonSegmentPrefix) is NOT
# duplicated here -- the build-time marker in CLOSEST_PAGE_MARKER is replaced
# with the contents of closest_page.js by render_not_found_html(). The same
# closest_page.js is also concat'd into picker.js by inject_version_picker.py,
# so cross-version sibling matching (in the picker dropdown) and same-version
# 404 fallback can never drift in behavior.
NOT_FOUND_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Page not found - PyRIT Documentation</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 40rem;
      margin: 4rem auto;
      padding: 0 1.5rem;
      color: #0f172a;
      line-height: 1.55;
    }}
    h1 {{ font-size: 2rem; margin: 0 0 0.5rem; }}
    .muted {{ color: #64748b; }}
    code {{
      background: #f1f5f9;
      padding: 0.1rem 0.4rem;
      border-radius: 0.25rem;
      font-size: 0.9em;
    }}
    .btn {{
      display: inline-block;
      padding: 0.6rem 1.1rem;
      background: #2563eb;
      color: #fff;
      text-decoration: none;
      border-radius: 0.5rem;
      margin-top: 1.5rem;
      font-weight: 500;
    }}
    .btn:hover {{ background: #1d4ed8; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #0f172a; color: #f8fafc; }}
      code {{ background: #1e293b; color: #f8fafc; }}
      .muted {{ color: #94a3b8; }}
    }}
  </style>
  <script>
  (function () {{
    var docsBase = "{docs_base}";
    var path = window.location.pathname;
    if (!path.startsWith(docsBase + "/")) return;
    var rest = path.slice(docsBase.length + 1);
    var slash = rest.indexOf("/");
    var slug = slash === -1 ? rest : rest.slice(0, slash);
    if (!slug) return;
    var relPath = slash === -1 ? "" : rest.slice(slash + 1);
    if (!relPath) return; // already at version root

    var versionBase = docsBase + "/" + slug + "/";

    // @@CLOSEST_PAGE_JS@@

    fetch(versionBase + "pages.json", {{ cache: "no-cache" }})
      .then(function (r) {{
        if (!r.ok) throw new Error("pages.json HTTP " + r.status);
        return r.json();
      }})
      .then(function (pages) {{
        var match = findClosestPage(pages, relPath);
        if (match === null) return;       // no manifest -- show static fallback
        if (match === "") return;         // only root matches -- show static fallback
        window.location.replace(versionBase + match);
      }})
      .catch(function () {{ /* no manifest available; leave fallback up */ }});
  }})();
  </script>
</head>
<body>
  <h1>Page not found</h1>
  <p class="muted">
    The page you requested doesn't exist in this version of the PyRIT docs.
  </p>
  <p>
    We tried looking for the closest parent section that exists, but didn't
    find one. The version picker in the bottom-right corner shows what's
    available across all versions.
  </p>
  <p><a class="btn" href="{home_href}">Go to PyRIT {default_slug} home</a></p>
  <p class="muted" style="margin-top: 3rem; font-size: 0.85rem;">
    Requested URL: <code id="requested-url"></code>
  </p>
  <script>
    document.getElementById("requested-url").textContent =
      window.location.pathname + window.location.search;
  </script>
</body>
</html>
"""


def render_redirect_html(title: str, target: str, link_text: str) -> str:
    return REDIRECT_HTML.format(title=title, target=target, link_text=link_text)


def render_not_found_html(base: str, default_slug: str) -> str:
    home_href = f"{base.rstrip('/')}/{default_slug}/"
    if not CLOSEST_PAGE_JS_PATH.is_file():
        raise FileNotFoundError(f"closest_page.js not found at {CLOSEST_PAGE_JS_PATH}")
    closest_page_js = CLOSEST_PAGE_JS_PATH.read_text(encoding="utf-8")
    html = NOT_FOUND_HTML_TEMPLATE.format(
        docs_base=base.rstrip("/"),
        home_href=home_href,
        default_slug=default_slug,
    )
    if CLOSEST_PAGE_MARKER not in html:
        raise RuntimeError(
            f"404 template is missing the {CLOSEST_PAGE_MARKER!r} marker; the closest-page algorithm cannot be inlined."
        )
    return html.replace(CLOSEST_PAGE_MARKER, closest_page_js)


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise FileNotFoundError(f"docs-versions config not found at {config_path}")
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def stage_versions(artifacts_dir: Path, dist_dir: Path) -> list[str]:
    """Move each ``artifacts/site-<slug>/`` into ``dist/<slug>/``. Returns staged slugs."""
    if not artifacts_dir.is_dir():
        raise FileNotFoundError(f"artifacts dir not found at {artifacts_dir}")
    staged: list[str] = []
    for entry in sorted(artifacts_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("site-"):
            continue
        slug = entry.name[len("site-") :]
        target = dist_dir / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(entry, target)
        staged.append(slug)
    return staged


def write_pages_manifest(version_dir: Path) -> int:
    pages = collect_pages(version_dir)
    (version_dir / "pages.json").write_text(json.dumps(pages, separators=(",", ":")), encoding="utf-8")
    return len(pages)


def write_versions_json(dist_dir: Path, cfg: dict[str, Any]) -> None:
    payload = {
        "default": cfg["default"],
        "stable": cfg["stable"],
        "versions": cfg["versions"],
    }
    (dist_dir / "versions.json").write_text(json.dumps(payload), encoding="utf-8")


def write_root_redirect(dist_dir: Path, base: str, default_slug: str) -> None:
    target = f"{base.rstrip('/')}/{default_slug}/"
    html = render_redirect_html(
        title="PyRIT Documentation",
        target=target,
        link_text=f"PyRIT documentation ({default_slug})",
    )
    (dist_dir / "index.html").write_text(html, encoding="utf-8")


def write_stable_redirect(dist_dir: Path, base: str, stable_slug: str) -> None:
    stable_dir = dist_dir / "stable"
    stable_dir.mkdir(parents=True, exist_ok=True)
    target = f"{base.rstrip('/')}/{stable_slug}/"
    html = render_redirect_html(
        title="PyRIT Documentation (stable)",
        target=target,
        link_text=f"PyRIT documentation ({stable_slug}, stable)",
    )
    (stable_dir / "index.html").write_text(html, encoding="utf-8")


def write_not_found(dist_dir: Path, base: str, default_slug: str) -> None:
    (dist_dir / "404.html").write_text(render_not_found_html(base, default_slug), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Directory containing site-<slug>/ subdirs (downloaded artifacts)",
    )
    parser.add_argument("--dist-dir", type=Path, required=True, help="Where to compose the final deploy tree")
    parser.add_argument("--config", type=Path, required=True, help="Path to docs-versions.yml")
    parser.add_argument("--base", type=str, required=True, help='URL base path the site is served from (e.g. "/PyRIT")')
    args = parser.parse_args(argv)

    artifacts_dir: Path = args.artifacts_dir.resolve()
    dist_dir: Path = args.dist_dir.resolve()
    config_path: Path = args.config.resolve()
    base: str = args.base.rstrip("/")

    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"[compose_docs_dist] staging artifacts from {artifacts_dir} into {dist_dir}")
    try:
        staged = stage_versions(artifacts_dir, dist_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not staged:
        print(f"error: no site-* subdirs found in {artifacts_dir}", file=sys.stderr)
        return 1

    for slug in staged:
        n = write_pages_manifest(dist_dir / slug)
        print(f"[compose_docs_dist] {slug}: wrote pages.json with {n} entries")

    write_versions_json(dist_dir, cfg)
    write_root_redirect(dist_dir, base, cfg["default"])
    write_stable_redirect(dist_dir, base, cfg["stable"])
    write_not_found(dist_dir, base, cfg["default"])
    print(f"[compose_docs_dist] wrote versions.json, root redirect, /stable/ redirect, 404.html (base={base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
