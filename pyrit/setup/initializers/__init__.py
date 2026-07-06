# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""PyRIT initializers package."""

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.parameter import Parameter
from pyrit.setup.initializers.load_default_datasets import LoadDefaultDatasets
from pyrit.setup.initializers.preload_scenario_metadata import PreloadScenarioMetadata
from pyrit.setup.initializers.scorers import ScorerInitializer
from pyrit.setup.initializers.targets import TargetInitializer
from pyrit.setup.initializers.techniques import TechniqueInitializer
from pyrit.setup.pyrit_initializer import PyRITInitializer

__all__ = [
    "Parameter",
    "PyRITInitializer",
    "TechniqueInitializer",
    "ScorerInitializer",
    "TargetInitializer",
    "LoadDefaultDatasets",
    "PreloadScenarioMetadata",
]


def __getattr__(name: str) -> type:
    if name == "InitializerParameter":
        print_deprecation_message(
            old_item="pyrit.setup.initializers.InitializerParameter",
            new_item=Parameter,
            removed_in="0.16.0",
        )
        return Parameter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
