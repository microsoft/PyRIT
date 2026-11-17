# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run stored ASSERT seed groups against a target and judge each conversation against its policy."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common import apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import (
    AssertGroupMetadata,
    AttackSeedGroup,
    BoundedDatasetSize,
    Parameter,
    ScenarioRunPlanGroupKind,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    ScoringExpectation,
    SeedPrompt,
    ViolatesAssertPolicy,
)
from pyrit.models.scenario_readiness import ScenarioConfigurationIssue, ScenarioReadiness
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import CapabilityName
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario.core import DatasetConstraintError, resolve_technique_factories
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetSourceKind
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
    check_target_compatibility,
    filter_seed_groups,
)
from pyrit.score import AssertJudgeScorer, AssertScoringDimension, TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pyrit.models import ScenarioDatasetSizeEstimate
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique

logger = logging.getLogger(__name__)


_ASSERT_PACKAGE = "assert_package"


def _build_assert_package_factory() -> AttackTechniqueFactory:
    """
    Build the scenario-local ``assert_package`` technique: red teaming with ASSERT's own tester.

    It is not registered globally, so only ``foundry.assert_policy`` offers it.

    Returns:
        AttackTechniqueFactory: The technique factory.
    """
    return AttackTechniqueFactory(
        name=_ASSERT_PACKAGE,
        attack_class=RedTeamingAttack,
        description=(
            "Red teaming whose tester uses ASSERT's own tester system prompt with each group's stored tester "
            "instructions, like an ASSERT scenario case. Groups without tester instructions are skipped."
        ),
        technique_tags=["multi_turn"],
    )


@AttackTechniqueRegistry.cache_scenario_technique_class
def _build_assert_policy_technique() -> type[ScenarioTechnique]:
    """
    Build the technique class from every registered attack technique plus ``assert_package``.

    ``assert_package`` is the default: it follows each group's stored tester instructions with
    ASSERT's tester prompt, like an ASSERT scenario case. The baseline sends each group's stored
    prompts, like an ASSERT prompt case.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    factories = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()
    factories[_ASSERT_PACKAGE] = _build_assert_package_factory()
    return AttackTechniqueRegistry.build_technique_class_from_factories(
        class_name="AssertPolicyTechnique",
        factories=list(factories.values()),
        default_names={_ASSERT_PACKAGE},
    )


class AssertPolicy(Scenario):
    """
    Run stored ASSERT seed groups against a target and judge each conversation against its policy.

    The baseline sends each group's stored system and user prompts once, like an ASSERT prompt
    case. The default technique, ``assert_package``, is ASSERT's scenario case: a red-teaming
    tester that follows the group's stored tester instructions for ``max_turns`` turns with
    ASSERT's own tester system prompt. Any registered technique can run too; ``red_teaming`` uses
    the same attack with the group's stored PyRIT tester prompt.

    Each conversation is judged against its group's stored policy. By default, a judge on the
    scoring target decides success by policy violation and also records overrefusal. Choose
    another objective scorer to judge other dimensions or execution traces. Blocked or errored
    target replies stay undetermined. Results are grouped by technique.
    """

    #: Version 3 passes each group's policy to the scorers as a condition.
    VERSION: int = 3
    #: ASSERT's tester prompts. The system prompt is ASSERT's text, and the first message and reply
    #: framing match ASSERT's tester loop, which shows each reply in ``<target_response>`` tags.
    TESTER_SYSTEM_PROMPT_PATH: ClassVar[Path] = EXECUTOR_RED_TEAM_PATH / "assert_tester_system.yaml"
    TESTER_FIRST_MESSAGE: ClassVar[str] = "Begin the conversation now with the user's first message only."
    TESTER_FEEDBACK_TEMPLATE: ClassVar[str] = "<target_response>\n{{ feedback_text }}\n</target_response>"
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        auxiliary_scorers: Sequence[TrueFalseScorer] | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the scenario.

        Args:
            adversarial_chat (PromptTarget | None): The adversarial chat for techniques that use one and
                don't set their own. Defaults to the registered adversarial target.
            objective_scorer (TrueFalseScorer | None): Decides success. A scorer that supports
                ``ViolatesAssertPolicy`` receives each group's policy. Defaults to an
                ``AssertJudgeScorer`` for policy violation on the scoring target.
            auxiliary_scorers (Sequence[TrueFalseScorer] | None): Further verdicts recorded on each
                result. Defaults to an overrefusal ``AssertJudgeScorer`` with the default objective
                scorer, and to none with a supplied one.
            scenario_result_id (str | None): Existing result to resume.
        """
        self._adversarial_chat = adversarial_chat
        self._supplied_auxiliary_scorers = None if auxiliary_scorers is None else tuple(auxiliary_scorers)
        self._auxiliary_scorers: tuple[TrueFalseScorer, ...] = ()
        self._repeated_ids: set[str] = set()
        super().__init__(
            version=self.VERSION,
            technique_class=_build_assert_policy_technique(),
            default_dataset_config=DatasetAttackConfiguration(max_per_dataset="all", max_total="all"),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
            uses_default_adversarial_target=adversarial_chat is None,
        )

    def get_readiness(self) -> ScenarioReadiness:
        """
        Report selected techniques whose scoring contract can't accept ASSERT's scoring.

        Returns:
            ScenarioReadiness: Execution-role bindings and configuration issues.
        """
        readiness = super().get_readiness()
        factories = self._get_readiness_factories()
        selected = {
            technique.value: factories[technique.value]
            for technique in self._scenario_techniques
            if technique.value in factories
        }
        incompatible = {
            name: reason for name, factory in selected.items() if (reason := self._get_scoring_incompatibility(factory))
        }
        usable = any(
            name not in incompatible
            and (
                self._objective_target is None
                or not self._get_target_requirements(factory).check_target(
                    target=self._objective_target, attack_class=factory.attack_class
                )
            )
            for name, factory in selected.items()
        )
        if incompatible and (self._incompatible_technique_policy is IncompatibleTechniquePolicy.RAISE or not usable):
            for name, reason in incompatible.items():
                readiness.issues.append(
                    ScenarioConfigurationIssue(
                        role="objective_scorer",
                        component=name,
                        reason=reason,
                        remediation="Select a technique that accepts ASSERT's scoring configuration.",
                    )
                )
        return readiness

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the ASSERT run settings.

        Returns:
            list[Parameter]: The turn count and the target's own instructions.
        """
        return [
            Parameter(
                name="max_turns",
                description=(
                    "Target turns for techniques that take a turn count and don't set their own. ASSERT's default is 6."
                ),
                param_type=int,
                default=6,
            ),
            Parameter(
                name="target_system_prompt",
                description=(
                    "Instructions the target application already uses, given to the judge only. "
                    "Rejected when a selected group stores its own system prompt."
                ),
                param_type=str | None,
                default=None,
                multiline=True,
            ),
        ]

    def _validate_runtime_configuration(self) -> None:
        super()._validate_runtime_configuration()
        if self.params["max_turns"] < 1:
            raise ValueError("max_turns must be a positive integer.")
        prompt = self.params.get("target_system_prompt")
        if prompt is not None and not prompt.strip():
            raise ValueError("target_system_prompt must not be blank.")

    def _has_no_datasets(self) -> bool:
        return self._dataset_config.source_kind is DatasetSourceKind.MEMORY and not self._dataset_config.dataset_names

    def _get_run_size_budget(self) -> ScenarioDatasetSizeEstimate:
        return BoundedDatasetSize(value=1) if self._has_no_datasets() else super()._get_run_size_budget()

    async def _estimate_run_size_async(self, *, budget: BoundedDatasetSize) -> ScenarioRunSizeEstimate:
        count, datasets = await self._get_dataset_size_for_estimate_async(budget=budget)
        components = [
            ScenarioRunSizeComponent(label=f"{technique.value} units", count=count)
            for technique in self._scenario_techniques
        ]
        if self._include_baseline:
            components.append(ScenarioRunSizeComponent(label="Baseline", count=count, is_baseline=True))
        note = (
            "At most one unit per selected group for each technique; groups a technique can't use are skipped. "
            f"Techniques that take a turn count make up to {self.params['max_turns']} target turns."
        )
        if self._has_no_datasets():
            datasets = []
            note = "No ASSERT dataset is selected; pass stored dataset names with --datasets. " + note
        return ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Approximate,
            total_attack_count=sum(component.count for component in components),
            components=components,
            datasets=datasets,
            note=note,
        )

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Read the selected groups once, validate them, then sample that same population.

        Returns:
            dict[str, list[AttackSeedGroup]]: Selected groups keyed by dataset.

        Raises:
            ValueError: If no dataset is selected, a group lacks ASSERT metadata or conflicts with
                ``target_system_prompt``, or a stored objective seed appears more than once.
            DatasetConstraintError: If the selected datasets contain no groups.
        """
        if self._has_no_datasets():
            raise ValueError(
                "foundry.assert_policy needs stored ASSERT seed groups. Pass their dataset names with "
                "--datasets (or the deprecated --dataset-names), or supply dataset_config in Python. "
                "Generate groups with AssertGenerator and store them with AssertProvider."
            )
        selection = await self._dataset_config._resolve_attack_groups_async()
        population = selection.select(apply_sampling=False)
        if not any(population.values()):
            raise DatasetConstraintError("The selected ASSERT datasets contain no seed groups.")
        groups = [group for dataset_groups in population.values() for group in dataset_groups]
        repeated_seeds = [seed_id for seed_id, count in Counter(g.objective.id for g in groups).items() if count > 1]
        if repeated_seeds:
            raise ValueError(
                f"The selected ASSERT seed groups include objective seed {repeated_seeds[0]} more than once. "
                "Select each stored group once."
            )
        for dataset_name, dataset_groups in population.items():
            for group in dataset_groups:
                self._validate_group(group=group, dataset_name=dataset_name)
        counts = Counter(group.logical_id for group in groups)
        self._repeated_ids = {logical_id for logical_id, count in counts.items() if count > 1}
        return selection.select(apply_sampling=True) if apply_sampling else population

    def _validate_group(self, *, group: AttackSeedGroup, dataset_name: str) -> None:
        """
        Require ASSERT metadata, and no stored system prompt when ``target_system_prompt`` is set.

        Raises:
            ValueError: If the group lacks ASSERT metadata or conflicts with ``target_system_prompt``.
        """
        self._read_metadata(group=group, dataset_name=dataset_name)
        if self.params.get("target_system_prompt") is not None and self._get_stored_system_prompt(group):
            raise ValueError(
                f"target_system_prompt is only for target-owned instructions, but a group in dataset "
                f"'{dataset_name}' stores its own system prompt. Remove target_system_prompt, or "
                "generate the groups with target_owns_instructions=True."
            )

    def _get_expectation(self, group: AttackSeedGroup) -> ScoringExpectation | None:
        """
        Pass the group's policy to an objective scorer that judges ASSERT policies.

        Auxiliary scorers receive it too when they support it.

        Returns:
            ScoringExpectation | None: The policy condition, or None for a scorer that doesn't read it.
        """
        if ViolatesAssertPolicy not in self._objective_scorer.get_condition_types():
            return None
        metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
        policy = ViolatesAssertPolicy(
            taxonomy=metadata.taxonomy,
            application_context=metadata.config.context,
            target_instructions=self._get_judged_system_prompt(group),
        )
        return ScoringExpectation(conditions=(policy,))

    @staticmethod
    def _read_metadata(*, group: AttackSeedGroup, dataset_name: str) -> AssertGroupMetadata:
        try:
            return AssertGroupMetadata.from_metadata(group.objective.metadata)
        except ValueError as error:
            raise ValueError(
                f"Dataset '{dataset_name}' contains a seed group without valid ASSERT generation metadata "
                f"('{group.objective.value[:80]}'). foundry.assert_policy only runs groups generated with "
                f"AssertGenerator and stored with AssertProvider. Details: {error}"
            ) from error

    @staticmethod
    def _get_stored_system_prompt(group: AttackSeedGroup) -> str | None:
        return next((prompt.value for prompt in group.prompts if prompt.role == "system"), None)

    def _get_judged_system_prompt(self, group: AttackSeedGroup) -> str | None:
        stored = self._get_stored_system_prompt(group)
        shared: str | None = self.params.get("target_system_prompt")
        return stored if stored is not None else shared

    @staticmethod
    def _get_generation_order(group: AttackSeedGroup) -> tuple[str, str]:
        return AssertGroupMetadata.from_metadata(group.objective.metadata).group_id, str(group.objective.id)

    def _get_unit_name(self, *, prefix: str, group: AttackSeedGroup) -> str:
        name = f"{prefix}__{group.logical_id}"
        if group.logical_id in self._repeated_ids:
            # Identical content shares a logical ID, and separate generation batches reuse group IDs,
            # so the stored objective seed ID keeps each name bound to its group across reads.
            name += f"__{group.objective.id}"
        return name

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        """
        Judge policy violation on the scoring target.

        Returns:
            TrueFalseScorer: The default ASSERT objective scorer.
        """
        return AssertJudgeScorer(chat_target=self._resolve_scoring_target())

    def _get_auxiliary_scorers(self) -> tuple[TrueFalseScorer, ...]:
        """
        Return the supplied auxiliary scorers, or an overrefusal judge beside the default objective.

        Returns:
            tuple[TrueFalseScorer, ...]: The auxiliary scorers.
        """
        if self._supplied_auxiliary_scorers is not None:
            return self._supplied_auxiliary_scorers
        if self._supplied_objective_scorer is not None:
            return ()
        target = self._objective_scorer.get_chat_target() or self._resolve_scoring_target()
        return (AssertJudgeScorer(chat_target=target, dimension=AssertScoringDimension.OVERREFUSAL),)

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        self._auxiliary_scorers = self._get_auxiliary_scorers()
        attacks = self._build_baselines(context=context) if context.include_baseline else []
        local = {_ASSERT_PACKAGE: _build_assert_package_factory()}
        for name, factory in resolve_technique_factories(context=context, extra_factories=local).items():
            if not check_target_compatibility(
                target=context.objective_target,
                attack_class=factory.attack_class,
                requirements=self._get_target_requirements(factory),
                technique_name=name,
                policy=context.incompatible_technique_policy,
            ):
                continue
            if reason := self._get_scoring_incompatibility(factory):
                message = f"Technique '{name}' is incompatible: {reason}"
                if context.incompatible_technique_policy is IncompatibleTechniquePolicy.RAISE:
                    raise IncompatibleTechniqueError(message)
                logger.warning("Skipping %s", message)
                continue
            factory = self._apply_turn_limit(factory)
            for dataset_name, groups in context.seed_groups_by_dataset.items():
                selected = filter_seed_groups(
                    seed_groups=groups,
                    requirements=factory.requirements.seed_group,
                    seed_technique=factory.seed_technique,
                    technique_name=name,
                    dataset_name=dataset_name,
                    policy=context.incompatible_technique_policy,
                    validate_group=(
                        self._require_tester_instructions
                        if name == _ASSERT_PACKAGE
                        else factory.validate_group_adversarial_prompts
                    ),
                )
                for group in sorted(selected, key=self._get_generation_order):
                    attacks.append(
                        await asyncio.to_thread(
                            self._build_unit, context=context, technique_name=name, factory=factory, group=group
                        )
                    )
        return attacks

    @staticmethod
    def _get_target_requirements(factory: AttackTechniqueFactory) -> TechniqueRequirements:
        """
        Require native multi-turn support for techniques that take a turn count.

        The judges read the whole conversation, which a target that doesn't keep multi-turn history
        would split into separate conversations.

        Returns:
            TechniqueRequirements: The factory's requirements, plus native multi-turn support if it takes turns.
        """
        requirements = factory.requirements
        if "max_turns" not in factory.get_effective_attack_kwargs():
            return requirements
        target = requirements.objective_target
        native = target.native_required | {CapabilityName.MULTI_TURN}
        return replace(requirements, objective_target=replace(target, native_required=native))

    @staticmethod
    def _get_scoring_incompatibility(factory: AttackTechniqueFactory) -> str | None:
        """
        Describe a narrowed scoring contract that cannot accept ASSERT's configuration.

        Args:
            factory (AttackTechniqueFactory): The selected attack technique.

        Returns:
            str | None: The incompatibility reason, or None when ASSERT's scoring is accepted.
        """
        required = factory.scoring_config_type
        if required is not None and not issubclass(AttackScoringConfig, required):
            return f"{required.__name__} cannot accept ASSERT's scoring configuration."
        return None

    def _apply_turn_limit(self, factory: AttackTechniqueFactory) -> AttackTechniqueFactory:
        """
        Apply ``max_turns`` to techniques whose attack takes a turn count and doesn't set one itself.

        Such attacks also judge only the final turn, so each conversation is judged once, as in ASSERT.
        Turn settings that a registered technique sets itself are kept.

        Returns:
            AttackTechniqueFactory: The factory, with the turn settings its attack accepts.
        """
        accepted = factory.get_effective_attack_kwargs()
        configured = factory.get_effective_settings().attack_kwargs
        defaults: dict[str, Any] = {"max_turns": self.params["max_turns"], "score_last_turn_only": True}
        if "max_turns" not in accepted:
            return factory
        attack_kwargs = {key: value for key, value in defaults.items() if key in accepted and key not in configured}
        return factory.with_attack_kwargs(attack_kwargs=attack_kwargs) if attack_kwargs else factory

    def _build_unit(
        self,
        *,
        context: ScenarioContext,
        technique_name: str,
        factory: AttackTechniqueFactory,
        group: AttackSeedGroup,
    ) -> AtomicAttack:
        extra = context.technique_converters.get(technique_name)
        adversarial_chat = (
            self._adversarial_chat if factory.uses_adversarial and factory.adversarial_chat is None else None
        )
        create_kwargs: dict[str, Any] = {
            "objective_target": context.objective_target,
            "attack_scoring_config": AttackScoringConfig(
                objective_scorer=self._objective_scorer,
                auxiliary_scorers=list(self._auxiliary_scorers),
                use_score_as_feedback=False,
            ),
            "adversarial_chat": adversarial_chat,
            "extra_request_converters": ConverterConfiguration.from_converters(converters=extra) if extra else None,
        }
        if technique_name == _ASSERT_PACKAGE:
            attack_technique = factory.create(**create_kwargs, **self._get_tester_prompts(group))
            # ASSERT's tester replaces the group's stored tester prompt; record the group's prompt on the
            # technique so the stored group still runs and resumes as-is.
            attack_technique = AttackTechnique(
                attack=attack_technique.attack,
                requirements=attack_technique.requirements,
                adversarial_prompts=group.adversarial_prompts,
            )
        else:
            attack_technique = factory.create(**create_kwargs, seed_group=group)
        return AtomicAttack(
            atomic_attack_name=self._get_unit_name(prefix=technique_name, group=group),
            display_group=technique_name,
            technique_name=technique_name,
            attack_technique=attack_technique,
            seed_groups=[group],
            adversarial_chat=adversarial_chat or factory.resolve_adversarial_chat(),
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
            expectation=self._get_expectation(group),
        )

    def _get_tester_prompts(self, group: AttackSeedGroup) -> dict[str, str | SeedPrompt]:
        """
        Fill ASSERT's tester prompt with the group's tester instructions.

        The attack fills in ``max_turns``, so the prompt states the turn limit the tester actually gets.

        Returns:
            dict[str, str | SeedPrompt]: The create-time tester prompts.
        """
        system_prompt = SeedPrompt.from_yaml_file(self.TESTER_SYSTEM_PROMPT_PATH).bind_template_parameters(
            description=self._get_tester_instructions(group)
        )
        return {
            "adversarial_system_prompt": system_prompt,
            "adversarial_seed_prompt": self.TESTER_FIRST_MESSAGE,
            "adversarial_prompt_template": self.TESTER_FEEDBACK_TEMPLATE,
        }

    @classmethod
    def _require_tester_instructions(cls, group: AttackSeedGroup) -> None:
        """
        Accept only groups that store tester instructions for ``assert_package``.

        Raises:
            IncompatibleTechniqueError: If the group has no stored tester instructions.
        """
        if cls._get_tester_instructions(group) is None:
            raise IncompatibleTechniqueError(
                "The group stores no tester instructions in AssertGroupMetadata.tester_instructions; "
                "regenerate legacy groups with include_adversarial_prompt=True or set that field explicitly."
            )

    @staticmethod
    def _get_tester_instructions(group: AttackSeedGroup) -> str | None:
        return AssertGroupMetadata.from_metadata(group.objective.metadata).tester_instructions

    def _build_baselines(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Send each group's stored system and user prompts once, judged against that group's policy.

        This is ASSERT's prompt case. Groups without a stored user prompt are skipped.

        Returns:
            list[AtomicAttack]: One baseline unit per group with a stored user prompt.
        """
        groups = sorted(context.seed_groups, key=self._get_generation_order)
        prompted = [group for group in groups if any(prompt.role == "user" for prompt in group.prompts)]
        if len(prompted) < len(groups):
            logger.warning("Baseline: skipping %d group(s) without a stored user prompt.", len(groups) - len(prompted))
        baselines = []
        for group in prompted:
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_scoring_config=AttackScoringConfig(
                    objective_scorer=self._objective_scorer, auxiliary_scorers=list(self._auxiliary_scorers)
                ),
            )
            baselines.append(
                AtomicAttack(
                    atomic_attack_name=self._get_unit_name(prefix="baseline", group=group),
                    display_group="baseline",
                    # Keep the stored group so baseline and technique results share its logical ID and resume.
                    # The prompt case doesn't use the group's tester prompts; this records that on the technique.
                    attack_technique=AttackTechnique(attack=attack, adversarial_prompts=group.adversarial_prompts),
                    seed_groups=[group],
                    objective_scorer=self._objective_scorer,
                    memory_labels=context.memory_labels,
                    group_kind=ScenarioRunPlanGroupKind.BASELINE,
                    expectation=self._get_expectation(group),
                )
            )
        return baselines
