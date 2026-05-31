# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Inject the PyRIT version picker into a built doc site.

Usage:
    python build_scripts/inject_version_picker.py \\
        --site-dir dist \\
        --base /PyRIT

The script:
    1. Copies picker.css into <site-dir>/_pyrit/ (still loaded via <link> tag).
    2. Walks every *.html under <site-dir> and injects, all into <head>:
        * <meta name="pyrit-docs-base" content="<base>">
        * <link rel="stylesheet" href="<base>/_pyrit/picker.css">
        * <script>...inlined picker.js...</script>
    3. Skips files that already contain our marker, so it's idempotent.

Why inline the JS instead of <script src="...">?

The myst-cli/Remix theme that PyRIT's docs use aggressively reconciles the
body during hydration and route transitions, removing any <script>, <meta>,
or <link> tags it didn't render server-side. By the time React touches our
inline script tag, the JS has already executed (during HTML parse) and its
history hooks + mutation observers are live. So the picker keeps re-mounting
itself even after React wipes the body. Verified locally on every PyRIT
route; matches the design constraint that broke the RTD addons flyout for
the same upstream reason.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

INJECT_MARKER = "<!-- pyrit-version-picker -->"
ASSETS_DIR_NAME = "_pyrit"
STYLES_RELATIVE_PATH = f"/{ASSETS_DIR_NAME}/picker.css"
ASSETS_SOURCE_DIR = Path(__file__).resolve().parent / "version_picker_assets"


def _head_block(base: str, picker_js: str) -> str:
    return (
        f"{INJECT_MARKER}\n"
        f'<meta name="pyrit-docs-base" content="{base}">\n'
        f'<link rel="stylesheet" href="{base}{STYLES_RELATIVE_PATH}">\n'
        f"<script>{picker_js}</script>\n"
    )


def _inject(html: str, base: str, picker_js: str) -> tuple[str, bool]:
    if INJECT_MARKER in html:
        return html, False
    head_block = _head_block(base, picker_js)
    new = html.replace("</head>", f"{head_block}</head>", 1) if "</head>" in html else head_block + html
    return new, True


def _copy_assets(site_dir: Path) -> None:
    if not ASSETS_SOURCE_DIR.is_dir():
        raise FileNotFoundError(f"picker assets not found at {ASSETS_SOURCE_DIR}")
    dst_dir = site_dir / ASSETS_DIR_NAME
    dst_dir.mkdir(parents=True, exist_ok=True)
    css_src = ASSETS_SOURCE_DIR / "picker.css"
    if not css_src.is_file():
        raise FileNotFoundError(f"picker.css not found at {css_src}")
    shutil.copy2(css_src, dst_dir / "picker.css")


def _load_picker_js() -> str:
    js_src = ASSETS_SOURCE_DIR / "picker.js"
    if not js_src.is_file():
        raise FileNotFoundError(f"picker.js not found at {js_src}")
    return js_src.read_text(encoding="utf-8")


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

    _copy_assets(site_dir)
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
