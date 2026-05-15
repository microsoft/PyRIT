# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer.scenario_result.pretty instead.
This re-export will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "ConsoleScenarioResultPrinter":
        _warnings.warn(
            "Importing ConsoleScenarioResultPrinter from pyrit.scenario.printer.console_printer is deprecated "
            "and will be removed in 0.16.0. Import from pyrit.printer.scenario_result.pretty instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from pyrit.printer.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

        return PrettyScenarioResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
