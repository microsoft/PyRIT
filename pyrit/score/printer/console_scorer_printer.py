# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer.scorer.console instead.
This re-export will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str):  # noqa: N807
    if name == "ConsoleScorerPrinter":
        _warnings.warn(
            "Importing ConsoleScorerPrinter from pyrit.score.printer.console_scorer_printer is deprecated "
            "and will be removed in 0.16.0. Import from pyrit.printer.scorer.console instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from pyrit.printer.scorer.console import ConsoleScorerPrinter

        return ConsoleScorerPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
