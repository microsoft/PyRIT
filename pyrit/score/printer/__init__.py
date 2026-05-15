# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output instead.

Scorer printers have moved to pyrit.output.scorer.
These re-exports will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    _deprecated = {
        "ConsoleScorerPrinter": "pyrit.output.scorer.pretty",
        "ScorerPrinter": "pyrit.output.scorer.base",
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
            from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter

            return PrettyScorerMemoryPrinter
        if name == "ScorerPrinter":
            from pyrit.output.scorer.base import ScorerPrinterBase

            return ScorerPrinterBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConsoleScorerPrinter",
    "ScorerPrinter",
]
