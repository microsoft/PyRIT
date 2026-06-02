# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Local dev server that mimics GitHub Pages URL resolution.

GitHub Pages serves extensionless URLs like /api/index by transparently
trying /api/index.html. Python's built-in http.server doesn't, so previewing
a myst-cli build locally produces spurious 404s for the theme's own
in-site links (e.g. the "API Reference" nav link, which points at
/api/index without a trailing slash or .html).

Run from the directory you want to serve:

    python build_scripts/preview_server.py [--port 8000] [--directory dist]

Resolution rules, in order:
    1. If the requested path is a file, serve it.
    2. If the requested path is a directory:
        a. If it has an index.html, serve that.
        b. Otherwise 404.
    3. If the requested path + ".html" is a file, serve that.
    4. Otherwise 404 -- and if a top-level 404.html exists, serve it
       with status 404 (matching GitHub Pages behavior).

This isn't a production server; it's only for local preview parity.
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
from pathlib import Path


class GitHubPagesHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: D401, N802 - inherited interface
        path = self.translate_path(self.path)
        # Default behavior: try the path as-is (file or dir-with-index).
        if os.path.isdir(path):
            index = os.path.join(path, "index.html")
            if os.path.isfile(index):
                return super().send_head()
            return self._serve_404()
        if os.path.isfile(path):
            return super().send_head()
        # Try appending .html for extensionless URLs.
        html_path = path + ".html"
        if os.path.isfile(html_path):
            # Temporarily rewrite self.path so SimpleHTTPRequestHandler resolves it.
            original = self.path
            # Strip query/fragment if any.
            base, _, rest = self.path.partition("?")
            base2, _, frag = base.partition("#")
            self.path = base2 + ".html" + (("?" + rest) if rest else "") + (("#" + frag) if frag else "")
            try:
                return super().send_head()
            finally:
                self.path = original
        return self._serve_404()

    def _serve_404(self):
        # Find a top-level 404.html relative to the served directory.
        served_root = Path(self.directory).resolve()  # type: ignore[arg-type]
        candidate = served_root / "404.html"
        # Also look one level down (e.g. dist/PyRIT/404.html) since our deploy
        # nests the site under /<repo>/.
        if not candidate.is_file():
            for child in served_root.iterdir():
                if child.is_dir():
                    nested = child / "404.html"
                    if nested.is_file():
                        candidate = nested
                        break
        if candidate.is_file():
            content = candidate.read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        # Fall back to a plain 404.
        self.send_error(404, "Not found")
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default 8000)")
    parser.add_argument("--directory", type=Path, default=Path.cwd(), help="Directory to serve (default cwd)")
    args = parser.parse_args(argv)

    served = args.directory.resolve()
    if not served.is_dir():
        print(f"error: --directory {served} does not exist", file=sys.stderr)
        return 1

    def factory(*a, **kw):
        return GitHubPagesHandler(*a, directory=str(served), **kw)

    print(f"Serving {served} on http://localhost:{args.port}/  (GH Pages emulation)")
    with socketserver.ThreadingTCPServer(("", args.port), factory) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
