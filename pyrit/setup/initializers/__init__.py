# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""PyRIT initializers package."""

from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.parameter import Parameter
from pyrit.setup.initializers.airt import AIRTInitializer
from pyrit.setup.initializers.components.scenarios import ScenarioTechniqueInitializer
from pyrit.setup.initializers.components.scorers import ScorerInitializer
from pyrit.setup.initializers.components.targets import TargetInitializer
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer
from pyrit.setup.initializers.scenarios.load_default_datasets import LoadDefaultDatasets
from pyrit.setup.initializers.scenarios.objective_list import ScenarioObjectiveListInitializer
from pyrit.setup.initializers.simple import SimpleInitializer

__all__ = [
    "Parameter",
    "PyRITInitializer",
    "AIRTInitializer",
    "ScenarioTechniqueInitializer",
    "ScorerInitializer",
    "TargetInitializer",
    "SimpleInitializer",
    "LoadDefaultDatasets",
    "ScenarioObjectiveListInitializer",
]


def __getattr__(name: str) -> type:
    if name == "InitializerParameter":
        print_deprecation_message(
            old_item="pyrit.setup.initializers.InitializerParameter",
            new_item=Parameter,
            removed_in="v0.16.0",
        )
        return Parameter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
