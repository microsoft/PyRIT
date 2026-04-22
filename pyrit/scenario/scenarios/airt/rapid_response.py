# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
RapidResponse scenario — technique-based rapid content-harms testing.

Strategies select **attack techniques** (PromptSending, RolePlay,
ManyShot, TAP). Datasets select **harm categories** (hate, fairness,
violence, …). Use ``--dataset-names`` to narrow which harm categories
to test.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


def _build_rapid_response_strategy() -> type[ScenarioStrategy]:
    """
    Build the RapidResponse strategy class dynamically from SCENARIO_TECHNIQUES.

    Reads the spec list (pure data) — no registry interaction or target resolution.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.tag_query import TagQuery
    from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

    return AttackTechniqueRegistry.build_strategy_class_from_specs(
        class_name="RapidResponseStrategy",
        specs=TagQuery.all("core").filter(SCENARIO_TECHNIQUES),
        aggregate_tags={
            "default": TagQuery.any_of("default"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class RapidResponse(Scenario):
    """
    Rapid Response scenario for content-harms testing.

    Tests model behavior across multiple harm categories using selectable attack
    techniques.
    """

    VERSION: int = 2
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the dynamically generated strategy class, building it on first access.

        Returns:
            type[ScenarioStrategy]: The RapidResponseStrategy enum class.
        """
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
        Return the default dataset configuration for AIRT harm categories.

        Returns:
            DatasetConfiguration: Configuration with standard harm-category datasets.
        """
        return DatasetConfiguration(
            dataset_names=[
                "airt_hate",
                "airt_fairness",
                "airt_violence",
                "airt_sexual",
                "airt_harassment",
                "airt_misinformation",
                "airt_leakage",
            ],
            max_dataset_size=4,
        )

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Rapid Response scenario.

        Args:
            objective_scorer: Scorer for evaluating attack success.
                Defaults to a composite Azure-Content-Filter + refusal
                scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=self.get_strategy_class(),
            scenario_result_id=scenario_result_id,
        )

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """
        Group results by harm category (dataset) rather than technique.

        Returns:
            str: The seed group name used as the display group.
        """
        return seed_group_name
