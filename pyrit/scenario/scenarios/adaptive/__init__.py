# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Adaptive scenario classes."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios.adaptive.adaptive_scenario import AdaptiveScenario
    from pyrit.scenario.scenarios.adaptive.dispatcher import (
        ADAPTIVE_ATTEMPT_LABEL,
        AdaptiveTechniqueDispatcher,
        TechniqueBundle,
    )
    from pyrit.scenario.scenarios.adaptive.selectors import (
        EpsilonGreedyTechniqueSelector,
        SelectorScope,
        TechniqueSelector,
    )
    from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "ADAPTIVE_ATTEMPT_LABEL": "pyrit.scenario.scenarios.adaptive.dispatcher",
    "AdaptiveScenario": "pyrit.scenario.scenarios.adaptive.adaptive_scenario",
    "AdaptiveTechniqueDispatcher": "pyrit.scenario.scenarios.adaptive.dispatcher",
    "EpsilonGreedyTechniqueSelector": "pyrit.scenario.scenarios.adaptive.selectors",
    "SelectorScope": "pyrit.scenario.scenarios.adaptive.selectors",
    "TechniqueBundle": "pyrit.scenario.scenarios.adaptive.dispatcher",
    "TechniqueSelector": "pyrit.scenario.scenarios.adaptive.selectors",
    "TextAdaptive": "pyrit.scenario.scenarios.adaptive.text_adaptive",
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
