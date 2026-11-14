# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Scenario suite catalog, builders, and aggregate initializer."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.setup.initializers.scenario_suites.horizontal_techniques import HorizontalTechniquesSuite
    from pyrit.setup.initializers.scenario_suites.scenario_suite_initializer import (
        ScenarioSuiteConfig,
        ScenarioSuiteInitializer,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "HorizontalTechniquesSuite": "pyrit.setup.initializers.scenario_suites.horizontal_techniques",
    "ScenarioSuiteConfig": "pyrit.setup.initializers.scenario_suites.scenario_suite_initializer",
    "ScenarioSuiteInitializer": "pyrit.setup.initializers.scenario_suites.scenario_suite_initializer",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(name=name, module_name=__name__, module_globals=globals(), exports=_LAZY_EXPORTS)


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
