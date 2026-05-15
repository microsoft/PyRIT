# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer instead.

Scenario result printers have moved to pyrit.printer.scenario_result.
These re-exports will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    _deprecated = {
        "ConsoleScenarioResultPrinter": "pyrit.printer.scenario_result.pretty",
        "ScenarioResultPrinter": "pyrit.printer.scenario_result.base",
    }
    if name in _deprecated:
        new_module = _deprecated[name]
        _warnings.warn(
            f"Importing {name} from pyrit.scenario.printer is deprecated and will be removed in 0.16.0. "
            f"Import from {new_module} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if name == "ConsoleScenarioResultPrinter":
            from pyrit.printer.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

            return PrettyScenarioResultMemoryPrinter
        if name == "ScenarioResultPrinter":
            from pyrit.printer.scenario_result.base import ScenarioResultPrinterBase

            return ScenarioResultPrinterBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConsoleScenarioResultPrinter",
    "ScenarioResultPrinter",
]
