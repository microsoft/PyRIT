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
from functools import cache
from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


@cache
def _build_rapid_response_strategy() -> type[ScenarioStrategy]:
    """
    Build the RapidResponse strategy class dynamically from the registered factories.

    Reads the singleton ``AttackTechniqueRegistry`` and filters to factories
    tagged ``core``.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.tag_query import TagQuery

    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = list(registry.get_factories_or_raise().values())

    return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="RapidResponseStrategy",
        factories=TagQuery.all("core").filter(factories),
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

        strategy_class = _build_rapid_response_strategy()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=strategy_class,
            default_strategy=strategy_class("default"),
            default_dataset_config=DatasetConfiguration(
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
            ),
            scenario_result_id=scenario_result_id,
        )

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """
        Group results by harm category (dataset) rather than technique.

        Returns:
            str: The seed group name used as the display group.
        """
        return seed_group_name
