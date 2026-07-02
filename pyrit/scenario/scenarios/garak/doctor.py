# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.prompt_converter import LeetspeakConverter, PolicyPuppetryConverter, PolicyPuppetryTemplate
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class DoctorStrategy(ScenarioStrategy):
    """
    Strategies for the Doctor scenario.

    Each strategy applies a Policy Puppetry prompt-injection template to the
    objective. ``PolicyPuppetry`` wraps the objective in the universal Dr House
    TV-script template; ``PolicyPuppetryLeet`` additionally leetspeak-encodes the
    templated prompt.
    """

    # Aggregate members
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})

    # Concrete strategies (values match the technique factory names)
    PolicyPuppetry = ("policy_puppetry", {"default"})
    PolicyPuppetryLeet = ("policy_puppetry_leet", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return the aggregate tags for the Doctor scenario."""
        return super().get_aggregate_tags() | {"default"}


# Doctor-specific technique factories. Kept local to this scenario (referenced from
# _build_atomic_attacks_async) so they don't pollute the global registry.
# The Dr House template is pinned (matching Garak's "Bypass" probe) so the
# scenario stays deterministic rather than using the converter's random default.
DOCTOR_FACTORIES: list[AttackTechniqueFactory] = [
    AttackTechniqueFactory(
        name="policy_puppetry",
        attack_class=PromptSendingAttack,
        strategy_tags=["default"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=PromptConverterConfiguration.from_converters(
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
        strategy_tags=["default"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(
                request_converters=PromptConverterConfiguration.from_converters(
                    converters=[
                        PolicyPuppetryConverter(prompt_template=PolicyPuppetryTemplate.DR_HOUSE.to_seed_prompt()),
                        LeetspeakConverter(),
                    ]
                )
            ),
        },
    ),
]


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

        super().__init__(
            version=self.VERSION,
            strategy_class=DoctorStrategy,
            default_strategy=DoctorStrategy.ALL,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["garak_doctor"]),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the Doctor atomic attacks from the selected Policy Puppetry techniques.

        Overrides the base extension point (rather than riding the base default cross-product
        via ``_get_attack_technique_factories``) so the Doctor-specific techniques stay local
        to this scenario and never enter the global registry. Delegates the technique × dataset
        cross-product to ``MatrixAtomicAttackBuilder``. The base owns baseline emission, so this
        passes ``include_baseline=False``.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        selected_techniques = {strategy.value for strategy in context.scenario_strategies}
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
            include_baseline=False,
        )
