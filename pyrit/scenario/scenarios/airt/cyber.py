# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING

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

_CYBER_TECHNIQUE_NAMES = {"red_teaming"}


@cache
def _build_cyber_strategy() -> type[ScenarioStrategy]:
    """
    Build the Cyber strategy class dynamically from the registered technique factories.

    Selects only the ``red_teaming`` factory from the singleton
    ``AttackTechniqueRegistry``. A plain ``PromptSendingAttack`` baseline is
    prepended automatically by ``Scenario._build_baseline_atomic_attack`` via
    ``BaselineAttackPolicy.Enabled``.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.tag_query import TagQuery

    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = registry.get_factories_or_raise()
    cyber_factories = [f for name, f in factories.items() if name in _CYBER_TECHNIQUE_NAMES]

    return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="CyberStrategy",
        factories=cyber_factories,
        aggregate_tags={
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

    @classmethod
    def get_override_composite_scorer_questions_path(cls) -> list[Path]:
        """
        Override true/false question paths for cyber objective scoring.

        Returns:
            Sequence[Path]: Paths to true/false question paths for cyber objective scoring.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "malware.yaml"]

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

        strategy_class = _build_cyber_strategy()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=strategy_class,
            default_strategy=strategy_class("all"),
            default_dataset_config=DatasetConfiguration(dataset_names=["airt_malware"], max_dataset_size=4),
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
