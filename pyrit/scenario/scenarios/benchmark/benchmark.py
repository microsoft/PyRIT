# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario

from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

def _build_benchmark_strategy(adversarial_models: list[PromptTarget]) -> type[ScenarioStrategy]:
    """
    Build the Benchmark strategy class dynamically from SCENARIO_TECHNIQUES.
    
    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    
    # TODO: Expand SCENARIO_TECHNIQUES using adversarial models. This requires
    # rebuilding the SCENARIO_TECHNIQUES list as it's a frozen dataclass.
    MODIFIED_SCENARIO_TECHNIQUES = ...
    return AttackTechniqueRegistry.build_strategy_class_from_specs(
            class_name="BenchmarkStrategy",
            specs=TagQuery.all("core").filter(SCENARIO_TECHNIQUES),
            aggregate_tags={
                "default": TagQuery.any_of("default"),
                "single_turn": TagQuery.any_of("single_turn"),
                "multi_turn": TagQuery.any_of("multi_turn"),
            },
        )
    
class Benchmark(Scenario):
    """
    Benchmarking scenario that compares the ASR of several different adversarial models.
    """
    
    VERSION: int = 1
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None
    
    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the dynamically generated strategy class, building it on first access.

        Returns:
            type[ScenarioStrategy]: The BenchmarkStrategy enum class.
        """
        raise NotImplementedError
        
        # TODO: Problem. This is a classmethod but we need instancemethod to get the
        # actual adversarial models (passed in constructor). 
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_rapid_response_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy member (``DEFAULT``).

        Returns:
            ScenarioStrategy: The default strategy value.
        """
        strategy_class = cls.get_strategy_class()
        return strategy_class("default")

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for benchmarking.

        Returns:
            DatasetConfiguration: Configuration with standard harm-category datasets.
        """
        return DatasetConfiguration(
            dataset_names=[
                "harmbench"
            ],
            max_dataset_size=8,
        )
        
    @apply_defaults
    def __init__(
        self,
        adversarial_models: list[PromptTarget]
    ) -> None:
        """
        TODO: Fill out docstring.
        TODO: Implement.
        """
        raise NotImplementedError
    
    def _build_display_group(self, *, adversarial_model_type: str) -> str:
        """
        TODO: Fill out docstring.
        TODO: Implement.
        """
        raise NotImplementedError

    
    def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        TODO: This is in the original requirements iirc, but seems
        to be missing from the closest analogue of RapidResponse. Why?
        TODO: Fill out docstring.
        """
        raise NotImplementedError
        