# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output.scorer.pretty instead.
This re-export will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "ConsoleScorerPrinter":
        _warnings.warn(
            "Importing ConsoleScorerPrinter from pyrit.score.printer.console_scorer_printer is deprecated "
            "and will be removed in 0.16.0. Import from pyrit.output.scorer.pretty instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter

        return PrettyScorerMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
