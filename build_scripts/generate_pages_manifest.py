# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Generate a pages.json manifest listing every rendered HTML page in a built site.

Usage:
    python build_scripts/generate_pages_manifest.py \\
        --site-dir dist/0.13.0 \\
        --output dist/0.13.0/pages.json

The manifest is a JSON array of URL paths (relative to the site root), each ending
with a trailing slash. The root page itself is represented as the empty string "".
Example:

    [
      "",
      "api/index/",
      "api/pyrit-analytics/",
      "code/scenarios/configuring-scenarios/",
      "code/scenarios/scenarios/",
      ...
    ]

The version picker uses this manifest to find the best fallback page when the
user switches to a version that doesn't have the exact URL the user was on.
Without the manifest the picker can only HEAD-probe ancestor paths, which means
empty navigation directories look the same as real pages -- and the picker
ends up sending users to the version's home page when there's actually a
closely related sibling section (e.g. ``code/scenarios/scenarios/`` next to a
missing ``code/scenarios/scenario-parameters/``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def collect_pages(site_dir: Path) -> list[str]:
    pages: list[str] = []
    for html in site_dir.rglob("index.html"):
        if not html.is_file():
            continue
        rel = html.parent.relative_to(site_dir).as_posix()
        if rel == ".":
            pages.append("")
        else:
            pages.append(rel + "/")
    pages.sort()
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-dir", type=Path, required=True, help="Root of the built docs site")
    parser.add_argument("--output", type=Path, required=True, help="Where to write pages.json")
    args = parser.parse_args(argv)

    site_dir: Path = args.site_dir.resolve()
    if not site_dir.is_dir():
        print(f"error: --site-dir {site_dir} does not exist", file=sys.stderr)
        return 1

    pages = collect_pages(site_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pages, separators=(",", ":")), encoding="utf-8")
    print(f"[generate_pages_manifest] wrote {len(pages)} pages to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
