# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
High-level scenario classes for running attack configurations.

Core classes can be imported directly from this module:
    from pyrit.scenario import Scenario, AtomicAttack, ScenarioStrategy

Specific scenarios should be imported from their subpackages:
    from pyrit.scenario.airt import ContentHarms, Cyber
    from pyrit.scenario.garak import Encoding
    from pyrit.scenario.foundry import RedTeamAgent
"""

import sys

from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult
from pyrit.scenario.core import (
    AtomicAttack,
    DatasetConfiguration,
    Scenario,
    ScenarioCompositeStrategy,
    ScenarioStrategy,
)

# Import scenario submodules directly and register them as virtual subpackages
# This allows: from pyrit.scenario.airt import ContentHarms
# without needing separate pyrit/scenario/airt/ directories
from pyrit.scenario.scenarios import airt as _airt_module
from pyrit.scenario.scenarios import foundry as _foundry_module
from pyrit.scenario.scenarios import garak as _garak_module

sys.modules["pyrit.scenario.airt"] = _airt_module
sys.modules["pyrit.scenario.garak"] = _garak_module
sys.modules["pyrit.scenario.foundry"] = _foundry_module

# Also expose as attributes for IDE support
airt = _airt_module
garak = _garak_module
foundry = _foundry_module

__all__ = [
    "AtomicAttack",
    "DatasetConfiguration",
    "Scenario",
    "ScenarioCompositeStrategy",
    "ScenarioStrategy",
    "ScenarioIdentifier",
    "ScenarioResult",
    "airt",
    "garak",
    "foundry",
]
