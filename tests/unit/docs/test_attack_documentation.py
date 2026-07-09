# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify all attacks are documented in the executor notebooks.

Mirrors ``test_converter_documentation.py``: every concrete ``AttackStrategy``
exported from ``pyrit.executor.attack`` must be mentioned by name somewhere under
``doc/code/executor`` (single-turn, multi-turn, compound, and workflow notebooks).
Some attacks are exported under multiple aliases (e.g. ``TAPAttack`` is an alias
of ``TreeOfAttacksWithPruningAttack``); an attack counts as documented if any of
its exported names appears. This keeps the docs in sync when a new attack is added.
"""

import inspect
import re
from pathlib import Path

import pytest

import pyrit.executor.attack as attack_module
from pyrit.executor.attack.core.attack_strategy import AttackStrategy

# tests/unit/docs -> tests/unit -> tests -> workspace_root
_EXECUTOR_DOC_PATH = Path(__file__).parent.parent.parent.parent / "doc" / "code" / "executor"


def get_all_attack_classes() -> dict[type, set[str]]:
    """Map each concrete attack class to the set of names it is exported under.

    Deduplicates by class object so aliases (e.g. ``TAPAttack`` /
    ``TreeOfAttacksWithPruningAttack``) collapse to a single entry.
    """
    classes: dict[type, set[str]] = {}
    for name in attack_module.__all__:
        obj = getattr(attack_module, name)
        if inspect.isclass(obj) and issubclass(obj, AttackStrategy) and not inspect.isabstract(obj):
            classes.setdefault(obj, set()).add(name)
    return classes


def get_attacks_mentioned_in_notebooks() -> str:
    """Return the concatenated text of every executor notebook (jupytext ``.py``)."""
    contents = []
    for notebook_file in sorted(_EXECUTOR_DOC_PATH.glob("*.py")):
        if notebook_file.name.startswith("_"):
            continue
        contents.append(notebook_file.read_text(encoding="utf-8"))
    return "\n".join(contents)


def test_all_attacks_are_documented():
    """Test that every concrete attack class is mentioned in an executor notebook."""
    all_attacks = get_all_attack_classes()
    notebook_text = get_attacks_mentioned_in_notebooks()

    undocumented = []
    for cls, names in all_attacks.items():
        if not any(re.search(rf"\b{re.escape(name)}\b", notebook_text) for name in names):
            undocumented.append(f"{cls.__name__} (aliases: {sorted(names)})")

    if undocumented:
        pytest.fail(
            f"The following attacks are not documented in any executor notebook:\n"
            f"{sorted(undocumented)}\n\n"
            f"Please add examples of these attacks to the appropriate notebook in doc/code/executor/ "
            f"(single-turn attacks in 1_single_turn, multi-turn in 2_multi_turn, compound in 4_compound)."
        )


if __name__ == "__main__":
    attacks = get_all_attack_classes()
    text = get_attacks_mentioned_in_notebooks()
    print(f"Total attacks: {len(attacks)}")
    for attack_cls, aliases in sorted(attacks.items(), key=lambda kv: kv[0].__name__):
        status = "OK" if any(re.search(rf"\b{re.escape(n)}\b", text) for n in aliases) else "MISSING"
        print(f"[{status}] {attack_cls.__name__} {sorted(aliases)}")
