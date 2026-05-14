# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer instead.

Attack result printers have moved to pyrit.printer.attack_result.
These re-exports will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str):  # noqa: N807
    _deprecated = {
        "ConsoleAttackResultPrinter": "pyrit.printer.attack_result.console",
        "AttackResultPrinter": "pyrit.printer.attack_result.base",
        "MarkdownAttackResultPrinter": "pyrit.executor.attack.printer.markdown_printer",
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
            from pyrit.printer.attack_result.console import ConsoleAttackMemoryPrinter

            return ConsoleAttackMemoryPrinter
        if name == "AttackResultPrinter":
            from pyrit.printer.attack_result.base import AttackResultPrinterBase

            return AttackResultPrinterBase
        if name == "MarkdownAttackResultPrinter":
            from pyrit.executor.attack.printer.markdown_printer import MarkdownAttackResultPrinter

            return MarkdownAttackResultPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AttackResultPrinter",
    "ConsoleAttackResultPrinter",
    "MarkdownAttackResultPrinter",
]
