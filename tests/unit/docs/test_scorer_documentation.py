# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify all scorers are documented in the scoring notebooks.

Mirrors ``test_converter_documentation.py``: every concrete scorer discovered by
the ``ScorerRegistry`` must be mentioned by name somewhere under
``doc/code/scoring`` (the ``true_false`` and ``float_scale`` scorer notebooks plus
the shared overview/combining/metrics notebooks in that folder). This keeps the
docs in sync when a new scorer is added.
"""

import re
from pathlib import Path

import pytest

from pyrit.registry import ScorerRegistry

# tests/unit/docs -> tests/unit -> tests -> workspace_root
_SCORING_DOC_PATH = Path(__file__).parent.parent.parent.parent / "doc" / "code" / "scoring"


def get_all_scorer_classes() -> set[str]:
    """Get all concrete scorer class names discovered by the registry."""
    registry = ScorerRegistry.get_registry_singleton()
    return {registry.get_class(name).__name__ for name in registry.get_class_names()}


def get_scorers_mentioned_in_notebooks() -> str:
    """Return the concatenated text of every scoring notebook (jupytext ``.py``)."""
    contents = []
    for notebook_file in sorted(_SCORING_DOC_PATH.glob("*.py")):
        if notebook_file.name.startswith("_"):
            continue
        contents.append(notebook_file.read_text(encoding="utf-8"))
    return "\n".join(contents)


def test_all_scorers_are_documented():
    """Test that every scorer class is mentioned in a scoring notebook."""
    all_scorers = get_all_scorer_classes()
    notebook_text = get_scorers_mentioned_in_notebooks()

    documented = {name for name in all_scorers if re.search(rf"\b{re.escape(name)}\b", notebook_text)}
    undocumented = all_scorers - documented

    # Scorers intentionally omitted from the scoring notebooks can be listed here
    # (e.g. abstract helpers). The registry already excludes abstract base classes,
    # so this is normally empty.
    exceptions: set[str] = set()

    undocumented -= exceptions

    if undocumented:
        pytest.fail(
            f"The following scorers are not documented in any scoring notebook:\n"
            f"{sorted(undocumented)}\n\n"
            f"Please add examples or a mention of these scorers to the appropriate notebook in "
            f"doc/code/scoring/ (true/false scorers in 1_true_false_scorers, float-scale scorers in "
            f"2_float_scale_scorers, or the combining/overview notebooks)."
        )


if __name__ == "__main__":
    scorers = get_all_scorer_classes()
    text = get_scorers_mentioned_in_notebooks()
    documented_names = {name for name in scorers if re.search(rf"\b{re.escape(name)}\b", text)}
    print(f"Total scorers: {len(scorers)}")
    print(f"Documented scorers: {len(documented_names)}")
    print(f"Undocumented: {sorted(scorers - documented_names)}")
