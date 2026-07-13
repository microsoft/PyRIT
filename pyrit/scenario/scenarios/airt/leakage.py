# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Leakage-specific technique catalog
# ---------------------------------------------------------------------------


@cache
def _leakage_factories() -> list[AttackTechniqueFactory]:
    """
    Return the AIRT source-owned leakage techniques (``first_letter``, ``image``).

    Imported lazily from the shared catalog (``techniques.airt``) to avoid an
    import cycle during ``pyrit.scenario`` package initialization. These live in
    the catalog but are not registered into the global registry — Leakage passes
    them explicitly, so the shared technique pool for other scenarios is unchanged.

    Returns:
        list[AttackTechniqueFactory]: The leakage-owned technique factories.
    """
    from pyrit.setup.initializers.techniques.airt import get_technique_factories

    return get_technique_factories()


@cache
def _build_leakage_technique() -> type[ScenarioTechnique]:
    """
    Build the Leakage technique class dynamically from core + leakage-specific factories.

    Combines core factories (from the registry) with leakage-unique factories
    (``first_letter``, ``image``) to provide the full set of attack techniques.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    core_factories = list(registry.get_factories_or_raise().values())
    all_factories = core_factories + _leakage_factories()
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="LeakageTechnique",
        factories=all_factories,
        aggregate_tags={
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
        default_technique_names={"role_play_movie_script", "many_shot", "first_letter", "image"},
    )


class Leakage(Scenario):
    """
    Leakage scenario implementation for PyRIT.

    This scenario tests how susceptible models are to leaking training data, PII, intellectual
    property, or other confidential information. Uses the registry/factory pattern to
    construct attack techniques.
    """

    VERSION: int = 2

    @classmethod
    def _get_additional_scoring_questions(cls) -> list[Path]:
        """
        Override true/false question paths for leakage objective scoring.

        Returns:
            Sequence[Path]: Paths to true/false question paths for leakage objective scoring.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "leakage.yaml"]

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["airt_leakage"]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the leakage scenario.

        Args:
            objective_scorer: Scorer for evaluating leakage detection.
                Defaults to a composite scorer (leakage detection + refusal backstop).
            scenario_result_id: Optional ID of an existing scenario result to resume.
        """
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()

        technique_class = _build_leakage_technique()

        super().__init__(
            version=self.VERSION,
            technique_class=technique_class,
            default_technique=technique_class("default"),
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["airt_leakage"], max_dataset_size=4),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the Leakage atomic attacks from the selected core + leakage techniques.

        Passes the leakage-specific factories (``first_letter``, ``image``) as
        ``extra_factories`` — kept local to this scenario so they don't pollute the global
        registry — and delegates the technique × dataset cross-product to
        ``build_matrix_atomic_attacks``. The base owns baseline emission.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        return build_matrix_atomic_attacks(
            context=context,
            objective_scorer=self._objective_scorer,
            technique_converters=self._technique_converters,
            extra_factories={factory.name: factory for factory in _leakage_factories()},
        )
