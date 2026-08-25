# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""High-level scenario classes for running attack configurations."""

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from types import ModuleType
from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models import ScenarioIdentifier, ScenarioResult
    from pyrit.models.parameter import Parameter
    from pyrit.scenario.core import (
        AtomicAttack,
        AttackTechnique,
        AttackTechniqueFactory,
        BaselineAttackPolicy,
        CompoundDatasetAttackConfiguration,
        DatasetAttackConfiguration,
        DatasetConfiguration,
        DatasetSourceKind,
        ResolvedDataset,
        Scenario,
        ScenarioTechnique,
    )
    from pyrit.scenario.scenarios import adaptive, airt, benchmark, foundry, garak

_SCENARIO_ALIASES = {
    "pyrit.scenario.adaptive": "pyrit.scenario.scenarios.adaptive",
    "pyrit.scenario.airt": "pyrit.scenario.scenarios.airt",
    "pyrit.scenario.benchmark": "pyrit.scenario.scenarios.benchmark",
    "pyrit.scenario.foundry": "pyrit.scenario.scenarios.foundry",
    "pyrit.scenario.garak": "pyrit.scenario.scenarios.garak",
}


class _ScenarioAliasLoader(importlib.abc.Loader):
    """Load a short scenario path through its canonical module."""

    def __init__(self, *, canonical_name: str) -> None:
        self._canonical_name = canonical_name

    def exec_module(self, module: ModuleType) -> None:
        canonical_module = importlib.import_module(self._canonical_name)
        sys.modules[module.__name__] = canonical_module


class _ScenarioAliasFinder(importlib.abc.MetaPathFinder):
    """Resolve short scenario package paths without importing scenario catalogs."""

    _pyrit_scenario_alias_finder = True

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        for alias_name, canonical_name in _SCENARIO_ALIASES.items():
            if fullname != alias_name and not fullname.startswith(f"{alias_name}."):
                continue

            canonical_fullname = canonical_name + fullname[len(alias_name) :]
            canonical_spec = importlib.util.find_spec(canonical_fullname)
            if canonical_spec is None:
                return None
            return importlib.util.spec_from_loader(
                fullname,
                _ScenarioAliasLoader(canonical_name=canonical_fullname),
                is_package=canonical_spec.submodule_search_locations is not None,
            )
        return None


if not any(getattr(finder, "_pyrit_scenario_alias_finder", False) for finder in sys.meta_path):
    sys.meta_path.insert(0, _ScenarioAliasFinder())

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AtomicAttack": "pyrit.scenario.core.atomic_attack",
    "AttackTechnique": "pyrit.scenario.core.attack_technique",
    "AttackTechniqueFactory": "pyrit.scenario.core.attack_technique_factory",
    "BaselineAttackPolicy": "pyrit.scenario.core.scenario",
    "CompoundDatasetAttackConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetAttackConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetConfiguration": "pyrit.scenario.core.dataset_configuration",
    "DatasetSourceKind": "pyrit.scenario.core.dataset_configuration",
    "Parameter": "pyrit.models.parameter",
    "ResolvedDataset": "pyrit.scenario.core.dataset_configuration",
    "Scenario": "pyrit.scenario.core.scenario",
    "ScenarioTechnique": "pyrit.scenario.core.scenario_technique",
    "ScenarioIdentifier": "pyrit.models.identifiers.scenario_identifier",
    "ScenarioResult": "pyrit.models.results.scenario_result",
    "adaptive": ("pyrit.scenario.scenarios.adaptive", None),
    "airt": ("pyrit.scenario.scenarios.airt", None),
    "benchmark": ("pyrit.scenario.scenarios.benchmark", None),
    "garak": ("pyrit.scenario.scenarios.garak", None),
    "foundry": ("pyrit.scenario.scenarios.foundry", None),
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
