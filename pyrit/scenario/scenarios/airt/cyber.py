# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.common.deprecation import print_deprecation_message  # Deprecated. Will be removed in 0.16.0.
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

_CYBER_TECHNIQUE_NAMES = {"prompt_sending", "red_teaming"}


def _build_cyber_strategy() -> type[ScenarioStrategy]:
    """
    Build the Cyber strategy class dynamically from SCENARIO_TECHNIQUES.

    Selects only ``prompt_sending`` and ``red_teaming`` techniques from
    the shared catalog.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.tag_query import TagQuery
    from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

    cyber_specs = [s for s in SCENARIO_TECHNIQUES if s.name in _CYBER_TECHNIQUE_NAMES]

    return AttackTechniqueRegistry.build_strategy_class_from_specs(  # type: ignore[ty:invalid-return-type]
        class_name="CyberStrategy",
        specs=cyber_specs,
        aggregate_tags={
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class Cyber(Scenario):
    """
    Cyber scenario implementation for PyRIT.

    This scenario tests how willing models are to exploit cybersecurity harms by generating
    malware. The Cyber class contains different variations of the malware generation
    techniques.
    """

    VERSION: int = 2
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    @classmethod
    def get_override_composite_scorer_questions_path(cls) -> list[Path]:
        """
        Override true/false question paths for cyber objective scoring.

        Returns:
            Sequence[Path]: Paths to true/false question paths for cyber objective scoring.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "malware.yaml"]

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the dynamically generated strategy class, building it on first access.

        Returns:
            type[ScenarioStrategy]: The CyberStrategy enum class.
        """
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_cyber_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy member (``ALL``).

        Returns:
            ScenarioStrategy: The ALL strategy value.
        """
        strategy_class = cls.get_strategy_class()
        return strategy_class("all")

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        Returns:
            DatasetConfiguration: Configuration with airt_malware dataset.
        """
        return DatasetConfiguration(dataset_names=["airt_malware"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
        include_baseline: bool | None = None,  # Deprecated. Will be removed in 0.16.0.
    ) -> None:
        """
        Initialize the cyber harms scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Objective scorer for malware detection. If not
                provided, defaults to a composite scorer using malware detection + refusal backstop.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
            include_baseline (bool | None): **Deprecated.** Will be removed in 0.16.0. Pass
                ``include_baseline`` to ``initialize_async`` instead.
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

        # Deprecated constructor-time baseline override. Will be removed in 0.16.0, along with
        # the include_baseline kwarg above.
        if include_baseline is not None:
            print_deprecation_message(
                old_item="Cyber(include_baseline=...)",
                new_item="Cyber.initialize_async(include_baseline=...)",
                removed_in="0.16.0",
            )
            self._legacy_include_baseline = include_baseline
