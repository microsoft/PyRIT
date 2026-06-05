# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output instead.

Attack result printers have moved to pyrit.output.attack_result.
These re-exports will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message


def __getattr__(name: str) -> type:
    if name == "ConsoleAttackResultPrinter":
        from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=PrettyAttackResultMemoryPrinter, removed_in="0.16.0"
        )
        return PrettyAttackResultMemoryPrinter
    if name == "AttackResultPrinter":
        from pyrit.output.attack_result.base import AttackResultPrinterBase

        print_deprecation_message(old_item=f"{__name__}.{name}", new_item=AttackResultPrinterBase, removed_in="0.16.0")
        return AttackResultPrinterBase
    if name == "MarkdownAttackResultPrinter":
        from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=MarkdownAttackResultMemoryPrinter, removed_in="0.16.0"
        )
        return MarkdownAttackResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AttackResultPrinter",
    "ConsoleAttackResultPrinter",
    "MarkdownAttackResultPrinter",
]
