# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output.attack_result.markdown instead.
This re-export will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message


def __getattr__(name: str) -> type:  # noqa: N807
    if name == "MarkdownAttackResultPrinter":
        from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=MarkdownAttackResultMemoryPrinter, removed_in="0.16.0"
        )
        return MarkdownAttackResultMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
