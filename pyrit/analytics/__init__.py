# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Analytics module for PyRIT conversation and result analysis."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.analytics.conversation_analytics import ConversationAnalytics
    from pyrit.analytics.result_analysis import AttackStats, analyze_results, get_cached_results_for_technique
    from pyrit.analytics.text_matching import ApproximateTextMatching, ExactTextMatching, TextMatching

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "analyze_results": "pyrit.analytics.result_analysis",
    "ApproximateTextMatching": "pyrit.analytics.text_matching",
    "AttackStats": "pyrit.analytics.result_analysis",
    "ConversationAnalytics": "pyrit.analytics.conversation_analytics",
    "ExactTextMatching": "pyrit.analytics.text_matching",
    "get_cached_results_for_technique": "pyrit.analytics.result_analysis",
    "TextMatching": "pyrit.analytics.text_matching",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
