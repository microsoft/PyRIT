# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
RapidResponse scenario — technique-based rapid content-harms testing.

Techniques select **attack techniques** (PromptSending, RolePlay,
ManyShot, TAP). Datasets select **harm categories** (hate, fairness,
violence, …). Use ``--dataset-names`` to narrow which harm categories
to test.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


@cache
def _build_rapid_response_technique() -> type[ScenarioTechnique]:
    """
    Build the RapidResponse technique class dynamically from the registered factories.

    Reads every technique registered in the singleton ``AttackTechniqueRegistry``
    and exposes all of them. Which techniques are available is decided by the
    active initializer (the registration gate), not narrowed again here.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = list(registry.get_factories_or_raise().values())

    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="RapidResponseTechnique",
        factories=factories,
        default_tags={"light"},
    )


class RapidResponse(Scenario):
    """
    Rapid Response scenario for content-harms testing.

    Tests model behavior across multiple harm categories using selectable attack
    techniques.
    """

    #: Bumped from 2 → 3 by dropping the ``core`` pool gate so the selectable
    #: technique pool (and the ``all`` aggregate) reflects whatever the initializer
    #: registered. ``use_cached`` only matches prior runs at the current ``VERSION``.
    VERSION: int = 3

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

        technique_class = _build_rapid_response_technique()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            technique_class=technique_class,
            default_dataset_config=CompoundDatasetAttackConfiguration.per_dataset(
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

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the technique × harm-category atomic attacks, grouped by harm category.

        Results group by harm category (the dataset name) rather than technique so per-category
        ASR rolls up naturally. The baseline is emitted by ``build_matrix_atomic_attacks`` when
        ``context.include_baseline`` is set (the base no longer emits one centrally), so this
        override never prepends one itself.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        return build_matrix_atomic_attacks(
            context=context,
            objective_scorer=self._objective_scorer,
            display_group_fn=lambda combo: combo.dataset_name,
            technique_converters=self._technique_converters,
        )
