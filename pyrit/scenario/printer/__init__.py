# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output instead.

Scenario result printers have moved to pyrit.output.scenario_result.
These re-exports will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "ConsoleScenarioResultPrinter":
        from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=PrettyScenarioResultMemoryPrinter, removed_in="0.16.0"
        )
        return PrettyScenarioResultMemoryPrinter
    if name == "ScenarioResultPrinter":
        from pyrit.output.scenario_result.base import ScenarioResultPrinterBase

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=ScenarioResultPrinterBase, removed_in="0.16.0"
        )
        return ScenarioResultPrinterBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConsoleScenarioResultPrinter",
    "ScenarioResultPrinter",
]
