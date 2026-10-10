# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

# Cyber curates its DEFAULT run to the technique(s) named here (see _build_cyber_technique).
# The pool of *available* techniques is not narrowed — cyber exposes whatever the active
# initializer has registered (like RapidResponse); the initializer is the single gate.
_CYBER_DEFAULT_TECHNIQUE_NAMES = {"red_teaming"}


@cache
def _build_cyber_technique() -> type[ScenarioTechnique]:
    """
    Build the Cyber technique class dynamically from the registered technique factories.

    Exposes every technique registered in the singleton ``AttackTechniqueRegistry``;
    which techniques are available is decided by the active initializer, not narrowed
    here. A plain ``PromptSendingAttack`` baseline is emitted by the
    matrix builder (``include_baseline=context.include_baseline``) via ``BaselineAttackPolicy.Enabled``.

    The ``DEFAULT`` aggregate is the curated default run — for Cyber it expands to
    ``red_teaming`` — while ``ALL`` selects the full registered pool.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = list(registry.get_factories_or_raise().values())

    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="CyberTechnique",
        factories=factories,
        default_names=_CYBER_DEFAULT_TECHNIQUE_NAMES,
    )


class Cyber(Scenario):
    """
    Cyber scenario implementation for PyRIT.

    This scenario tests how willing models are to exploit cybersecurity harms by generating
    malware. The Cyber class contains different variations of the malware generation
    techniques.
    """

    #: Bumped from 2 → 3 by dropping the ``core`` pool gate so the selectable
    #: technique pool (and the ``all`` aggregate) reflects whatever the initializer
    #: registered. ``use_cached`` only matches prior runs at the current ``VERSION``.
    VERSION: int = 3

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
    ) -> None:
        """
        Initialize the cyber harms scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Objective scorer for malware detection. If not
                provided, defaults to a composite scorer using malware detection + refusal backstop.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        technique_class = _build_cyber_technique()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            technique_class=technique_class,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["airt_malware"], max_dataset_size=4),
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the technique × dataset atomic attacks for Cyber, grouped by technique.

        The baseline is emitted by ``build_matrix_atomic_attacks`` when ``context.include_baseline``
        is set (the base no longer emits one centrally), so this override never prepends one itself.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        return build_matrix_atomic_attacks(
            context=context,
            objective_scorer=self._objective_scorer,
            technique_converters=self._technique_converters,
        )
