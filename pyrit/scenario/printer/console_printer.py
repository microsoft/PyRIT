# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output.scenario_result.pretty instead.
This re-export will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "ConsoleScenarioResultPrinter":
        from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=PrettyScenarioResultMemoryPrinter, removed_in="0.16.0"
        )
        return PrettyScenarioResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
