# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Selector protocol and selector implementations."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy import EpsilonGreedyTechniqueSelector
    from pyrit.scenario.scenarios.adaptive.selectors.technique_selector import SelectorScope, TechniqueSelector

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "EpsilonGreedyTechniqueSelector": "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy",
    "SelectorScope": "pyrit.scenario.scenarios.adaptive.selectors.technique_selector",
    "TechniqueSelector": "pyrit.scenario.scenarios.adaptive.selectors.technique_selector",
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
