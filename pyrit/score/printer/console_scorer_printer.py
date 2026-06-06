# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deprecated: Import from pyrit.output.scorer.pretty instead.
This re-export will be removed in 0.16.0.
"""

from pyrit.common.deprecation import print_deprecation_message


def __getattr__(name: str) -> type:
    if name == "ConsoleScorerPrinter":
        from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter

        print_deprecation_message(
            old_item=f"{__name__}.{name}", new_item=PrettyScorerMemoryPrinter, removed_in="0.16.0"
        )
        return PrettyScorerMemoryPrinter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
