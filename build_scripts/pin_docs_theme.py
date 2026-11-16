# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Pin the MyST site theme in a checked-out documentation configuration."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_IMMUTABLE_BOOK_THEME_URL = re.compile(r"https://github\.com/myst-templates/book-theme/archive/[0-9a-f]{40}\.zip")
_BOOK_THEME_LINE = re.compile(r"^(?P<indent>[ \t]*)template:[ \t]*book-theme[ \t]*$", re.MULTILINE)


def pin_docs_theme(*, config_path: Path, template: str) -> None:
    if not _IMMUTABLE_BOOK_THEME_URL.fullmatch(template):
        raise ValueError("template must be a myst-templates/book-theme archive URL pinned to a full commit SHA")
    content = config_path.read_text(encoding="utf-8")
    updated, replacements = _BOOK_THEME_LINE.subn(rf"\g<indent>template: {template}", content)
    if replacements != 1:
        raise ValueError(f"{config_path}: expected exactly one unpinned 'template: book-theme', found {replacements}")
    config_path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Path to the checked-out doc/myst.yml file.")
    parser.add_argument("--template", required=True, help="Immutable book-theme archive URL.")
    args = parser.parse_args(argv)

    try:
        pin_docs_theme(config_path=args.config, template=args.template)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
