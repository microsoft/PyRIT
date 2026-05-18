# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for deprecated printer import paths.

The printer classes formerly at ``pyrit.executor.attack.printer.*``,
``pyrit.score.printer.*``, and ``pyrit.scenario.printer.*`` moved to
``pyrit.output.*``. The old paths are thin shims that emit a
``DeprecationWarning`` and forward to the new class.

These tests verify the shims are wired correctly. Functional tests for the
printers themselves live alongside this file under ``tests/unit/output/``.
"""

import importlib
import warnings

import pytest

from pyrit.output.attack_result.base import AttackResultPrinterBase
from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter
from pyrit.output.scenario_result.base import ScenarioResultPrinterBase
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.output.scorer.base import ScorerPrinterBase
from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter

# Each entry: deprecated module path, deprecated attribute name, expected new class.
DEPRECATED_PATHS = [
    # pyrit.executor.attack.printer
    ("pyrit.executor.attack.printer", "AttackResultPrinter", AttackResultPrinterBase),
    ("pyrit.executor.attack.printer", "ConsoleAttackResultPrinter", PrettyAttackResultMemoryPrinter),
    ("pyrit.executor.attack.printer", "MarkdownAttackResultPrinter", MarkdownAttackResultMemoryPrinter),
    (
        "pyrit.executor.attack.printer.console_printer",
        "ConsoleAttackResultPrinter",
        PrettyAttackResultMemoryPrinter,
    ),
    (
        "pyrit.executor.attack.printer.markdown_printer",
        "MarkdownAttackResultPrinter",
        MarkdownAttackResultMemoryPrinter,
    ),
    # pyrit.score.printer
    ("pyrit.score.printer", "ScorerPrinter", ScorerPrinterBase),
    ("pyrit.score.printer", "ConsoleScorerPrinter", PrettyScorerMemoryPrinter),
    ("pyrit.score.printer.console_scorer_printer", "ConsoleScorerPrinter", PrettyScorerMemoryPrinter),
    # pyrit.scenario.printer
    ("pyrit.scenario.printer", "ScenarioResultPrinter", ScenarioResultPrinterBase),
    ("pyrit.scenario.printer", "ConsoleScenarioResultPrinter", PrettyScenarioResultMemoryPrinter),
    (
        "pyrit.scenario.printer.console_printer",
        "ConsoleScenarioResultPrinter",
        PrettyScenarioResultMemoryPrinter,
    ),
]

DEPRECATED_MODULES = sorted({module for module, _, _ in DEPRECATED_PATHS})


@pytest.mark.parametrize("module_path,old_name,new_class", DEPRECATED_PATHS)
def test_deprecated_path_forwards_to_new_class(module_path, old_name, new_class):
    module = importlib.import_module(module_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cls = getattr(module, old_name)

    assert cls is new_class
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecation_warnings, f"No DeprecationWarning emitted for {module_path}.{old_name}"
    message = str(deprecation_warnings[0].message)
    assert old_name in message
    assert module_path in message
    assert new_class.__module__ in message


@pytest.mark.parametrize("module_path", DEPRECATED_MODULES)
def test_unknown_attribute_raises(module_path):
    module = importlib.import_module(module_path)
    with pytest.raises(AttributeError, match="NotARealPrinter"):
        module.NotARealPrinter  # noqa: B018
