# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
High-level scenario classes for running attack configurations.

Core classes can be imported directly from this module:
    from pyrit.scenario import Scenario, AtomicAttack, ScenarioTechnique

Specific scenarios should be imported from their subpackages:
    from pyrit.scenario.airt import RapidResponse, Cyber
    from pyrit.scenario.garak import Encoding
    from pyrit.scenario.foundry import RedTeamAgent
"""

import importlib
import pkgutil
import sys
from types import ModuleType

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

# Import scenario submodules directly and register them as virtual subpackages
# This allows: from pyrit.scenario.airt import Jailbreak
# without needing separate pyrit/scenario/airt/ directories
from pyrit.scenario.scenarios import adaptive as _adaptive_module
from pyrit.scenario.scenarios import airt as _airt_module
from pyrit.scenario.scenarios import benchmark as _benchmark_module
from pyrit.scenario.scenarios import foundry as _foundry_module
from pyrit.scenario.scenarios import garak as _garak_module


def _register_scenario_alias(short_name: str, canonical_module: ModuleType) -> None:
    """
    Alias ``pyrit.scenario.<short_name>`` (and every submodule) to ``canonical_module``.

    A bare ``sys.modules[short] = canonical`` only fixes ``import
    pyrit.scenario.<short>`` itself. Accessing a submodule via the alias path
    (``pyrit.scenario.<short>.<sub>``) re-runs the submodule's file under the
    aliased fully-qualified name and produces a duplicate class object — which
    silently breaks ``isinstance`` against the canonical class. To prevent that,
    we walk the canonical package's submodules eagerly and register every one
    under both names so the second import returns the same module object.
    """
    sys.modules[f"pyrit.scenario.{short_name}"] = canonical_module
    canonical_prefix = canonical_module.__name__ + "."
    short_prefix = f"pyrit.scenario.{short_name}."
    for module_info in pkgutil.walk_packages(canonical_module.__path__, canonical_prefix):
        submodule = importlib.import_module(module_info.name)
        sys.modules[short_prefix + module_info.name[len(canonical_prefix) :]] = submodule


_register_scenario_alias("adaptive", _adaptive_module)
_register_scenario_alias("airt", _airt_module)
_register_scenario_alias("benchmark", _benchmark_module)
_register_scenario_alias("foundry", _foundry_module)
_register_scenario_alias("garak", _garak_module)

# Also expose as attributes for IDE support
adaptive = _adaptive_module
airt = _airt_module
benchmark = _benchmark_module
garak = _garak_module
foundry = _foundry_module

__all__ = [
    "AtomicAttack",
    "AttackTechnique",
    "AttackTechniqueFactory",
    "BaselineAttackPolicy",
    "CompoundDatasetAttackConfiguration",
    "DatasetAttackConfiguration",
    "DatasetConfiguration",
    "DatasetSourceKind",
    "Parameter",
    "ResolvedDataset",
    "Scenario",
    "ScenarioTechnique",
    "ScenarioIdentifier",
    "ScenarioResult",
    "adaptive",
    "airt",
    "benchmark",
    "garak",
    "foundry",
]
