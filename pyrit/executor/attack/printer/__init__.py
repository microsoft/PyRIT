# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer instead.

Attack result printers have moved to pyrit.printer.attack_result.
These re-exports will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    _deprecated = {
        "ConsoleAttackResultPrinter": "pyrit.printer.attack_result.pretty",
        "MarkdownAttackResultPrinter": "pyrit.printer.attack_result.markdown",
        "AttackResultPrinter": "pyrit.printer.attack_result.base",
    }
    if name in _deprecated:
        new_module = _deprecated[name]
        _warnings.warn(
            f"Importing {name} from pyrit.executor.attack.printer is deprecated and will be removed in 0.16.0. "
            f"Import from {new_module} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if name == "ConsoleAttackResultPrinter":
            from pyrit.printer.attack_result.pretty import PrettyAttackResultMemoryPrinter

            return PrettyAttackResultMemoryPrinter
        if name == "AttackResultPrinter":
            from pyrit.printer.attack_result.base import AttackResultPrinterBase

            return AttackResultPrinterBase
        if name == "MarkdownAttackResultPrinter":
            from pyrit.printer.attack_result.markdown import MarkdownAttackResultMemoryPrinter

            return MarkdownAttackResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AttackResultPrinter",
    "ConsoleAttackResultPrinter",
    "MarkdownAttackResultPrinter",
]
