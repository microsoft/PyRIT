# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer.attack_result.console instead.
This re-export will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "ConsoleAttackResultPrinter":
        _warnings.warn(
            "Importing ConsoleAttackResultPrinter from pyrit.executor.attack.printer.console_printer is deprecated "
            "and will be removed in 0.16.0. Import from pyrit.printer.attack_result.console instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from pyrit.printer.attack_result.console import ConsoleAttackResultPrinter

        return ConsoleAttackResultPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
