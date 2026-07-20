# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.converter import LeetspeakConverter, PolicyPuppetryConverter, PolicyPuppetryTemplate
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


# Doctor-specific technique factories. Kept local to this scenario (referenced from
# _build_atomic_attacks_async) so they don't pollute the global registry — the Policy
# Puppetry templates are pinned to this probe rather than being general-purpose.
# The Dr House template is pinned (matching Garak's "Bypass" probe) so the
# scenario stays deterministic rather than using the converter's random default.
DOCTOR_FACTORIES: list[AttackTechniqueFactory] = [
    AttackTechniqueFactory(
        name="policy_puppetry",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(
                    converters=[
                        PolicyPuppetryConverter(prompt_template=PolicyPuppetryTemplate.DR_HOUSE.to_seed_prompt())
                    ]
                )
            ),
        },
    ),
    AttackTechniqueFactory(
        name="policy_puppetry_leet",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(
                    converters=[
                        PolicyPuppetryConverter(prompt_template=PolicyPuppetryTemplate.DR_HOUSE.to_seed_prompt()),
                        LeetspeakConverter(),
                    ]
                )
            ),
        },
    ),
]


# Doctor's technique enum is generated from DOCTOR_FACTORIES via the shared factory
# generator (like the registry-driven scenarios) rather than hand-written. Both
# techniques are the scenario default, so DEFAULT and ALL coincide today; ALL exists
# so a future non-default technique would diverge from DEFAULT without another change.
# Built lazily and cached (like the other dynamically-generated scenarios) so every
# Doctor instance shares one enum class; the public ``DoctorTechnique`` symbol is
# resolved from here via the garak package ``__getattr__``.
@cache
def _build_doctor_technique() -> type[ScenarioTechnique]:
    """
    Generate the Doctor technique enum from ``DOCTOR_FACTORIES``.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="DoctorTechnique",
        factories=DOCTOR_FACTORIES,
        default_names={"policy_puppetry", "policy_puppetry_leet"},
    )


class Doctor(Scenario):
    """
    Doctor scenario implementation for PyRIT.

    Ports Garak's "doctor" probe, which implements the HiddenLayer Policy Puppetry
    universal bypass: a post-instruction-hierarchy prompt injection that frames a
    harmful request as policy/configuration (a fictional medical-drama script or a
    healthcare-chatbot interaction config) to override safety guardrails.

    Each objective from the ``garak_doctor`` dataset is wrapped in a Policy Puppetry
    template via ``PolicyPuppetryConverter`` and sent to the target. Responses are
    scored for non-refusal (mirroring Garak's ``mitigation.MitigationBypass`` detector).

    Reference: [@hiddenlayer2025policypuppetry]
    (https://hiddenlayer.com/innovation-hub/novel-universal-bypass-for-all-major-llms/)
    """

    VERSION: int = 1

    # Template-dominated like the Jailbreak scenario: baseline is supported but off
    # by default since the unmodified objective is a weak comparison point here.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Disabled

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["garak_doctor"]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Doctor scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for detecting a successful bypass
                (non-refusal). If not provided, defaults to the scenario's default objective scorer.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()

        technique_class = _build_doctor_technique()

        super().__init__(
            version=self.VERSION,
            technique_class=technique_class,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["garak_doctor"]),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the Doctor atomic attacks from the selected Policy Puppetry techniques.

        Builds the Doctor-specific technique factories locally (so they never enter the global
        registry) and delegates the technique × dataset cross-product to
        ``MatrixAtomicAttackBuilder``. Baseline emission is the scenario's responsibility, so this
        passes ``include_baseline=context.include_baseline`` (Doctor defaults its policy to
        ``Disabled``).

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        selected_techniques = {technique.value for technique in context.scenario_techniques}
        technique_factories = {
            factory.name: factory for factory in DOCTOR_FACTORIES if factory.name in selected_techniques
        }

        builder = MatrixAtomicAttackBuilder(
            objective_target=context.objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )
        return builder.build(
            technique_factories=technique_factories,
            dataset_groups=context.seed_groups_by_dataset,
            include_baseline=context.include_baseline,
        )
