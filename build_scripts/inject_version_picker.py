# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Inject the PyRIT version picker into a built doc site.

Usage:
    python build_scripts/inject_version_picker.py \\
        --site-dir dist \\
        --base /PyRIT

The script walks every *.html under <site-dir> and injects (all into <head>):
    * <meta name="pyrit-docs-base" content="<base>">
    * <script>...inlined picker.js...</script>  (CSS is bundled inside)

Idempotent: files that already contain our marker are skipped.

Why inline the JS instead of <script src="..."> AND why no <link>?

The myst-cli/Remix theme that PyRIT's docs use aggressively reconciles the
body and head during hydration and route transitions, removing any <script>,
<meta>, <link>, or <style> tags it didn't render server-side. We work around
that by:

  * Inlining the <script> in <head> so the JS runs during HTML parse, before
    React hydrates. By the time React removes the <script> tag, the JS has
    already wired up its history hooks and mutation observers.
  * Bundling the CSS into the JS as a string and injecting it via a JS-created
    <style> tag at mount time. <link rel="stylesheet"> would get removed by
    React just like <script src>, and removing a <link> removes its styles.
  * Re-mounting the picker and re-injecting the <style> if React strips them
    later (handled inside picker.js).

Verified locally on a real PyRIT build: picker mounts on / and on
/code/framework/, survives SPA navigation, and keeps its styling.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

INJECT_MARKER = "<!-- pyrit-version-picker -->"
ASSETS_SOURCE_DIR = Path(__file__).resolve().parent / "version_picker_assets"

# The same marker string is used in compose_docs_dist.py's 404 template. Both
# consumers replace this token with the contents of closest_page.js at build
# time so the findClosestPage/commonSegmentPrefix algorithm has exactly one
# source of truth -- see closest_page.js for the rationale.
CLOSEST_PAGE_MARKER = "// @@CLOSEST_PAGE_JS@@"


def _head_block(base: str, picker_js: str) -> str:
    return f'{INJECT_MARKER}\n<meta name="pyrit-docs-base" content="{base}">\n<script>{picker_js}</script>\n'


def _inject(html: str, base: str, picker_js: str) -> tuple[str, bool]:
    if INJECT_MARKER in html:
        return html, False
    head_block = _head_block(base, picker_js)
    new = html.replace("</head>", f"{head_block}</head>", 1) if "</head>" in html else head_block + html
    return new, True


def _load_picker_js() -> str:
    js_src = ASSETS_SOURCE_DIR / "picker.js"
    closest_page_src = ASSETS_SOURCE_DIR / "closest_page.js"
    if not js_src.is_file():
        raise FileNotFoundError(f"picker.js not found at {js_src}")
    if not closest_page_src.is_file():
        raise FileNotFoundError(f"closest_page.js not found at {closest_page_src}")
    picker = js_src.read_text(encoding="utf-8")
    closest = closest_page_src.read_text(encoding="utf-8")
    if CLOSEST_PAGE_MARKER not in picker:
        raise RuntimeError(
            f"picker.js is missing the {CLOSEST_PAGE_MARKER!r} marker; the closest-page algorithm cannot be inlined."
        )
    return picker.replace(CLOSEST_PAGE_MARKER, closest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-dir", type=Path, required=True, help="Root of the built docs site (e.g. dist/)")
    parser.add_argument("--base", type=str, required=True, help='URL base path the site is served from (e.g. "/PyRIT")')
    args = parser.parse_args(argv)

    site_dir: Path = args.site_dir.resolve()
    base: str = args.base.rstrip("/")
    if not site_dir.is_dir():
        print(f"error: --site-dir {site_dir} does not exist", file=sys.stderr)
        return 1

    picker_js = _load_picker_js()

    html_files = list(site_dir.rglob("*.html"))
    modified = 0
    skipped = 0
    for path in html_files:
        try:
            html = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        new_html, did_change = _inject(html, base, picker_js)
        if did_change:
            path.write_text(new_html, encoding="utf-8")
            modified += 1
        else:
            skipped += 1

    print(f"[inject_version_picker] modified={modified} skipped={skipped} total={len(html_files)} base={base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
