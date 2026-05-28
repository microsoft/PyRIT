# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pre-commit guard against Sphinx reST cross-reference roles in source.

PyRIT docs render docstrings through MyST (jupyter-book 2), not Sphinx, so
reST roles like ``:class:`Foo``` show up as raw literal text in the built
site. The standing convention (style-guide.instructions.md) is to
use plain double-backticks; ``build_scripts/gen_api_md.py`` then auto-links
known PyRIT symbols at render time.

This hook flags any newly introduced reST role inside ``pyrit/`` so it can
be replaced before landing. Run it manually with::

    uv run python build_scripts/check_no_rest_roles.py

or rely on the ``check-no-rest-roles`` pre-commit hook in
``.pre-commit-config.yaml``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Roles flagged by this guard. Mirrors the list in the style guide. The
# pattern matches the leading colon, role name, and the opening backtick of
# the role argument (e.g. ``:class:`Foo```), so backticked code spans that
# happen to start with a colon character are not caught.
_REST_ROLE_RE = re.compile(r":(?:class|func|meth|mod|attr|data|exc|obj|ref|py:[a-z]+):`")


def _check_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _REST_ROLE_RE.search(line):
            findings.append((lineno, line.rstrip()))
    return findings


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return 0

    failures: list[tuple[Path, list[tuple[int, str]]]] = []
    for raw in args:
        path = Path(raw)
        if path.suffix != ".py":
            continue
        findings = _check_file(path)
        if findings:
            failures.append((path, findings))

    if not failures:
        return 0

    print("\nreST cross-reference roles are not allowed in PyRIT source.")
    print("PyRIT renders docstrings with MyST, not Sphinx — these roles show")
    print("up as raw literal text in the built docs.\n")
    print("Replace ``:class:`Foo``` / ``:func:`bar``` / ``:meth:`Baz.do``` etc.")
    print("with plain double-backticks (``Foo``). build_scripts/gen_api_md.py")
    print("auto-links known PyRIT symbols at render time.\n")
    for path, findings in failures:
        for lineno, snippet in findings:
            print(f"  {path}:{lineno}: {snippet}")
    print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
