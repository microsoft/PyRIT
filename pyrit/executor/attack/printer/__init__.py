# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.printer instead.

Attack result printers have moved to pyrit.printer.attack_result.
These re-exports are provided for backward compatibility.
"""

from pyrit.common.deprecation import print_deprecation_message
from pyrit.executor.attack.printer.attack_result_printer import AttackResultPrinter
from pyrit.printer.attack_result.console import ConsoleAttackResultPrinter

# MarkdownAttackResultPrinter is not yet refactored, keep the old import
from pyrit.executor.attack.printer.markdown_printer import MarkdownAttackResultPrinter

__all__ = [
    "AttackResultPrinter",
    "ConsoleAttackResultPrinter",
    "MarkdownAttackResultPrinter",
]
