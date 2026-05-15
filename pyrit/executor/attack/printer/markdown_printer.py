# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output.attack_result.markdown instead.
This re-export will be removed in 0.16.0.
"""

import warnings as _warnings


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "MarkdownAttackResultPrinter":
        _warnings.warn(
            "Importing MarkdownAttackResultPrinter from pyrit.executor.attack.printer.markdown_printer is deprecated "
            "and will be removed in 0.16.0. Import from pyrit.output.attack_result.markdown instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        return MarkdownAttackResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
