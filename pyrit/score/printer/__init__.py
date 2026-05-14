# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer instead.

Scorer printers have moved to pyrit.printer.scorer.
These re-exports will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str):  # noqa: N807
    _deprecated = {
        "ConsoleScorerPrinter": "pyrit.printer.scorer.console",
        "ScorerPrinter": "pyrit.printer.scorer.base",
    }
    if name in _deprecated:
        new_module = _deprecated[name]
        _warnings.warn(
            f"Importing {name} from pyrit.score.printer is deprecated and will be removed in 0.16.0. "
            f"Import from {new_module} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if name == "ConsoleScorerPrinter":
            from pyrit.printer.scorer.console import ConsoleScorerPrinter

            return ConsoleScorerPrinter
        if name == "ScorerPrinter":
            from pyrit.printer.scorer.base import ScorerPrinterBase

            return ScorerPrinterBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConsoleScorerPrinter",
    "ScorerPrinter",
]
