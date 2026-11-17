# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Regenerate the scorer capability table page from the scorer classes.

The page is built from class-level contracts only, so generating it constructs no scorer
and needs no credentials.

Usage:
    python -m build_scripts.generate_scorer_capability_table          # rewrite the page
    python -m build_scripts.generate_scorer_capability_table --check  # fail if it is stale
"""

import sys
from pathlib import Path

PAGE = Path("doc") / "code" / "scoring" / "scorer_capabilities.md"

INTRO = """# Scorer capabilities

What each public scorer class accepts and guarantees, generated from the classes' own
declarations. Generating it constructs no scorer, so it needs no credentials or targets.

Scenarios that select an objective scorer from a `CompatibleScorers` pool check the same
contracts on configured scorers, through `scorer.capabilities`. Some depend on
configuration, so a configured scorer is the authority: a composite takes its evidence and
conditions from the scorers it wraps, and a message scorer evaluates `MatchesObjective`
only when it is configured to judge an objective.

- **Result** is the score family the scorer returns.
- **Evidence** is the scorable types it accepts, such as `MessageScorable`.
- **Conditions** is the condition type it evaluates, such as `ToolsCalled`.
- **Float feedback** says whether each complete true/false result keeps a float value in
  `[0, 1]`, which attacks that rank results need. TAP requires it.

Regenerate this page with `python -m build_scripts.generate_scorer_capability_table`.
"""


def render_page() -> str:
    """
    Render the full page.

    Returns:
        str: The Markdown page.
    """
    from pyrit.score.scorer_catalog import render_scorer_capability_table

    return f"{INTRO}\n{render_scorer_capability_table()}\n"


def main(argv: list[str]) -> int:
    """
    Write the page, or check that it is current.

    Returns:
        int: 0 when the page was written or is current, 1 when ``--check`` finds it stale.
    """
    expected = render_page()
    if "--check" in argv:
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if current != expected:
            print(f"{PAGE} is out of date. Run: python -m build_scripts.generate_scorer_capability_table")
            return 1
        return 0
    PAGE.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
