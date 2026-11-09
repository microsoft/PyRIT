# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run stored ASSERT seed groups against a target and judge each conversation against its policy."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import (
    AssertGroupMetadata,
    AttackSeedGroup,
    BoundedDatasetSize,
    ComponentIdentifier,
    ConversationScorable,
    MessageScorable,
    Parameter,
    ScenarioRunPlanGroupKind,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    SeedGroupRequirements,
)
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetConstraintError,
    DatasetSourceKind,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_target_defaults import get_default_scorer_target
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
    check_target_compatibility,
    filter_seed_groups,
)
from pyrit.score import (
    AssertJudgmentMode,
    AssertScoringDimension,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
    build_assert_policy_scorer,
)
from pyrit.score.scorer import _set_snapshot_participation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import ScenarioDatasetSizeEstimate, Scorable, ScorableUnion, Score, ScoringExpectation
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import Scorer
    from pyrit.score.scorer import SnapshotParticipation

logger = logging.getLogger(__name__)


class AssertTechnique(ScenarioTechnique):
    """How stored ASSERT groups reach the target."""

    ALL = ("all", {"all"})
    SingleTurn = (
        "single_turn",
        {"single_turn"},
        "Send the group's stored system and user prompts once, like an ASSERT prompt case.",
    )
    MultiTurn = (
        "multi_turn",
        {"multi_turn"},
        "Let an adversarial chat follow the group's tester instructions, like an ASSERT scenario case.",
    )

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return the aggregate technique tags."""
        return {"all"}


class _AssertPlaceholderScorer(TrueFalseScorer):
    """Scenario identity until the selected groups define their judges; it never scores."""

    SUPPORTED_SCORABLE_TYPES: ClassVar[tuple[type[Scorable], ...]] = (MessageScorable, ConversationScorable)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        raise RuntimeError("AssertPolicy scores each group with its own ASSERT judges, not the placeholder.")


class _AssertJudgedScorer(TrueFalseScorer):
    """Judge a conversation only when ASSERT would; otherwise record why no verdict was reached."""

    SKIP_REASON_KEY: ClassVar[str] = "assert_scoring_skipped"
    TARGET_INPUT_REFUSED: ClassVar[str] = "target_input_refused"
    TARGET_ERROR: ClassVar[str] = "target_error"
    SKIP_RATIONALES: ClassVar[dict[str, str]] = {
        TARGET_INPUT_REFUSED: "A target reply was blocked, so ASSERT does not judge this conversation.",
        TARGET_ERROR: "A target reply was an error, so ASSERT does not judge this conversation.",
    }

    def __init__(self, *, scorer: TrueFalseScorer, dimension: AssertScoringDimension) -> None:
        """
        Wrap one ASSERT conversation judge.

        Args:
            scorer (TrueFalseScorer): The ASSERT conversation judge.
            dimension (AssertScoringDimension): The failure the judge detects.
        """
        self._scorer = scorer
        self._dimension = dimension
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"skipped_target_replies": [self.TARGET_INPUT_REFUSED, self.TARGET_ERROR]},
            sub_scorers=[self._scorer.get_identifier()],
        )

    def get_chat_target(self) -> PromptTarget | None:
        """
        Return the judge target.

        Returns:
            PromptTarget | None: The wrapped judge's target.
        """
        return self._scorer.get_chat_target()

    def with_scorer_block_policy(self, *, raise_if_scorer_blocks: bool) -> Scorer:
        """
        Apply the blocked-judge policy to the wrapped judge.

        Args:
            raise_if_scorer_blocks (bool): The policy to apply.

        Returns:
            Scorer: ``self`` when the judge is unchanged, otherwise a copy wrapping the updated judge.
        """
        scoped = self._scorer.with_scorer_block_policy(raise_if_scorer_blocks=raise_if_scorer_blocks)
        if scoped is self._scorer:
            return self
        return _AssertJudgedScorer(scorer=cast("TrueFalseScorer", scoped), dimension=self._dimension)

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        return (self._scorer,)

    async def _filter_matching_scores_by_snapshot_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        used_observation_ids: dict[str, set[str]],
        participation_by_score: dict[str, SnapshotParticipation | None],
    ) -> list[Score]:
        return await self._filter_matching_scores_by_children_async(
            scores=scores,
            scorable=scorable,
            expectation=expectation,
            used_observation_ids=used_observation_ids,
            participation_by_score=participation_by_score,
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        conversation_id = await self._get_conversation_id_async(scorable=scorable)
        reason = await self._get_skip_reason_async(conversation_id=conversation_id) if conversation_id else None
        if reason:
            skipped = self._build_undetermined_score(
                rationale=self.SKIP_RATIONALES[reason],
                description="ASSERT does not judge this conversation.",
                objective=expectation.objective if expectation else None,
                score_category=[self._dimension.value],
                score_metadata={self.SKIP_REASON_KEY: reason},
            )
            return [self._anchor(score=skipped, scorable=scorable, child_scores={})]
        scores = await self._scorer._score_nested_async(
            scorable=scorable, expectation=self._scorer._select_expectation(expectation=expectation)
        )
        return [
            self._anchor(score=self._create_wrapper_score(score), scorable=scorable, child_scores={0: score})
            for score in scores
        ]

    def _anchor(self, *, score: Score, scorable: Scorable, child_scores: dict[int, Score]) -> Score:
        score.scorable = cast("ScorableUnion", scorable)
        score.message_piece_id = self._piece_id_from_scorable(scorable)
        _set_snapshot_participation(score=score, child_scores=child_scores)
        return score

    async def _get_conversation_id_async(self, *, scorable: Scorable) -> str | None:
        if isinstance(scorable, ConversationScorable):
            return scorable.conversation_id
        if not isinstance(scorable, MessageScorable):
            return None
        pieces = await self._memory.get_message_pieces_async(prompt_ids=list(scorable.message_piece_ids))
        return pieces[0].conversation_id if pieces else None

    async def _get_skip_reason_async(self, *, conversation_id: str) -> str | None:
        for piece in await self._memory.get_message_pieces_async(conversation_id=conversation_id):
            if piece.api_role != "assistant" or piece.is_simulated:
                continue
            if piece.is_blocked():
                return self.TARGET_INPUT_REFUSED
            if piece.has_error() or piece.converted_value_data_type == "error":
                return self.TARGET_ERROR
        return None


@dataclass(frozen=True)
class _GroupScoring:
    """The judges and display stance for one stored group."""

    policy: TrueFalseScorer
    overrefusal: TrueFalseScorer
    permissible: bool


class AssertPolicy(Scenario):
    """
    Run stored ASSERT seed groups against a target and judge each conversation against its policy.

    ``single_turn`` sends each group's stored system and user prompts once. ``multi_turn`` lets an
    adversarial chat follow the group's tester instructions for ``max_turns`` turns, opening with
    the stored user prompt when the group has one. A judge built from each group's taxonomy then
    reads the whole conversation twice: once for a policy violation (the objective score) and once
    for overrefusal (an auxiliary score). Conversations with a blocked or errored target reply are
    not judged and stay undetermined. Results are grouped by technique and by whether the group's
    assigned category is permissible.
    """

    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Disabled
    SUPPORTS_TECHNIQUE_CONVERTERS: ClassVar[bool] = False

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget | None = None,
        judge_target: PromptTarget | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the scenario.

        Args:
            adversarial_chat (PromptTarget | None): The tester for multi-turn groups. Defaults to the
                registered adversarial target, resolved when attacks are built.
            judge_target (PromptTarget | None): The ASSERT judge. Defaults to the registered scorer
                target, resolved when the selected groups are read.
            scenario_result_id (str | None): Existing result to resume.
        """
        self._adversarial_chat = adversarial_chat
        self._judge_target = judge_target
        self._judges_by_policy: dict[str, tuple[TrueFalseScorer, TrueFalseScorer]] = {}
        self._repeated_ids: set[str] = set()
        super().__init__(
            version=self.VERSION,
            technique_class=AssertTechnique,
            default_dataset_config=DatasetAttackConfiguration(max_per_dataset="all", max_total="all"),
            objective_scorer=_AssertPlaceholderScorer(),
            scenario_result_id=scenario_result_id,
            uses_default_adversarial_target=adversarial_chat is None,
        )

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the ASSERT run settings.

        Returns:
            list[Parameter]: Turn count, judgment mode, and target-owned instructions.
        """
        return [
            Parameter(
                name="max_turns",
                description="Target turns in each multi-turn conversation. ASSERT's default is 6.",
                param_type=int,
                default=6,
            ),
            Parameter(
                name="judgment_mode",
                description="category_validated records per-category judgments; direct returns only the verdict.",
                param_type=AssertJudgmentMode,
                default=AssertJudgmentMode.CATEGORY_VALIDATED,
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
            "At most one unit per selected group for each technique; groups without that technique's stored "
            f"inputs are skipped. Each conversation gets two judge calls, and multi-turn units make up to "
            f"{self.params['max_turns']} target and adversarial calls."
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
        Read the selected groups once, build their judges, then sample that same population.

        Judges come from the unsampled population because resume reads it unsampled, so the
        scenario identity stays the same.

        Returns:
            dict[str, list[AttackSeedGroup]]: Selected groups keyed by dataset.

        Raises:
            ValueError: If no dataset is selected, or a stored objective seed appears more than once.
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
        await asyncio.to_thread(self._configure_judges, population=population)
        counts = Counter(group.logical_id for group in groups)
        self._repeated_ids = {logical_id for logical_id, count in counts.items() if count > 1}
        return selection.select(apply_sampling=True) if apply_sampling else population

    def _configure_judges(self, *, population: dict[str, list[AttackSeedGroup]]) -> None:
        """
        Build one judge pair per policy and replace the placeholder identity with all of them.

        Raises:
            ValueError: If a group lacks ASSERT metadata, or stores a system prompt while
                ``target_system_prompt`` is set.
        """
        judge_target = self._judge_target or get_default_scorer_target()
        mode = AssertJudgmentMode(self.params["judgment_mode"])
        self._judges_by_policy = {}
        for dataset_name, groups in population.items():
            for group in groups:
                metadata = self._read_metadata(group=group, dataset_name=dataset_name)
                if self.params.get("target_system_prompt") is not None and self._get_stored_system_prompt(group):
                    raise ValueError(
                        f"target_system_prompt is only for target-owned instructions, but a group in dataset "
                        f"'{dataset_name}' stores its own system prompt. Remove target_system_prompt, or "
                        "generate the groups with target_owns_instructions=True."
                    )
                system_prompt = self._get_judged_system_prompt(group)
                key = self._get_policy_key(metadata=metadata, system_prompt=system_prompt)
                if key not in self._judges_by_policy:
                    self._judges_by_policy[key] = self._build_judges(
                        judge_target=judge_target, metadata=metadata, system_prompt=system_prompt, mode=mode
                    )
        self._objective_scorer = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.OR,
            scorers=[scorer for judges in self._judges_by_policy.values() for scorer in judges],
        )
        self._objective_scorer_identifier = self._objective_scorer.get_identifier()

    def _get_scoring(self, group: AttackSeedGroup) -> _GroupScoring:
        metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
        key = self._get_policy_key(metadata=metadata, system_prompt=self._get_judged_system_prompt(group))
        policy, overrefusal = self._judges_by_policy[key]
        category = next(
            item for item in metadata.taxonomy.behavior_categories if item.name == metadata.behavior_category
        )
        return _GroupScoring(policy=policy, overrefusal=overrefusal, permissible=category.permissible)

    @staticmethod
    def _get_policy_key(*, metadata: AssertGroupMetadata, system_prompt: str | None) -> str:
        return json.dumps([metadata.taxonomy.model_dump(mode="json"), metadata.config.context, system_prompt])

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

    @staticmethod
    def _build_judges(
        *,
        judge_target: PromptTarget,
        metadata: AssertGroupMetadata,
        system_prompt: str | None,
        mode: AssertJudgmentMode,
    ) -> tuple[TrueFalseScorer, TrueFalseScorer]:
        """
        Build the policy-violation and overrefusal judges for one policy.

        A blocked judge reply yields an undetermined score rather than an error, as in ASSERT.

        Returns:
            tuple[TrueFalseScorer, TrueFalseScorer]: The policy-violation and overrefusal judges.
        """
        judges = [
            _AssertJudgedScorer(
                scorer=cast(
                    "TrueFalseScorer",
                    build_assert_policy_scorer(
                        target=judge_target,
                        taxonomy=metadata.taxonomy,
                        dimension=dimension,
                        application_context=metadata.config.context,
                        target_system_prompt=system_prompt,
                        judgment_mode=mode,
                    ).with_scorer_block_policy(raise_if_scorer_blocks=False),
                ),
                dimension=dimension,
            )
            for dimension in (AssertScoringDimension.POLICY_VIOLATION, AssertScoringDimension.OVERREFUSAL)
        ]
        return judges[0], judges[1]

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        factories = self._build_technique_factories()
        attacks = self._build_baselines(context=context) if context.include_baseline else []
        for technique in cast("tuple[AssertTechnique, ...]", context.scenario_techniques):
            factory = factories[technique]
            if not check_target_compatibility(
                target=context.objective_target,
                attack_class=factory.attack_class,
                requirements=factory.requirements,
                technique_name=technique.value,
                policy=context.incompatible_technique_policy,
            ):
                continue
            for dataset_name, groups in context.seed_groups_by_dataset.items():
                selected = self._select_groups(
                    technique=technique, factory=factory, dataset_name=dataset_name, groups=groups, context=context
                )
                for group in sorted(selected, key=self._get_generation_order):
                    attacks.append(
                        await asyncio.to_thread(
                            self._build_unit,
                            context=context,
                            technique=technique,
                            factory=factory,
                            group=group,
                            name=self._get_unit_name(prefix=technique.value, group=group),
                        )
                    )
        return attacks

    def _build_technique_factories(self) -> dict[AssertTechnique, AttackTechniqueFactory]:
        return {
            AssertTechnique.SingleTurn: AttackTechniqueFactory(
                name=AssertTechnique.SingleTurn.value,
                attack_class=PromptSendingAttack,
                description=AssertTechnique.SingleTurn.description,
                technique_tags=["single_turn"],
            ),
            AssertTechnique.MultiTurn: AttackTechniqueFactory(
                name=AssertTechnique.MultiTurn.value,
                attack_class=RedTeamingAttack,
                description=AssertTechnique.MultiTurn.description,
                technique_tags=["multi_turn"],
                attack_kwargs={"max_turns": self.params["max_turns"], "score_last_turn_only": True},
                adversarial_chat=self._adversarial_chat,
                use_score_as_feedback=False,
            ),
        }

    @staticmethod
    def _select_groups(
        *,
        technique: AssertTechnique,
        factory: AttackTechniqueFactory,
        dataset_name: str,
        groups: list[AttackSeedGroup],
        context: ScenarioContext,
    ) -> list[AttackSeedGroup]:
        """
        Keep groups that store the inputs a technique needs, under the incompatibility policy.

        Returns:
            list[AttackSeedGroup]: Groups the technique can run.

        Raises:
            IncompatibleTechniqueError: If a group lacks the inputs and the policy is RAISE.
        """
        single_turn = technique is AssertTechnique.SingleTurn
        if single_turn:
            compatible = [group for group in groups if any(prompt.role == "user" for prompt in group.prompts)]
        else:
            compatible = [group for group in groups if group.adversarial_prompts is not None]
        missing = len(groups) - len(compatible)
        if missing:
            needed = "user prompt" if single_turn else "adversarial prompts"
            message = f"Technique '{technique.value}', dataset '{dataset_name}': {missing} group(s) have no {needed}."
            if context.incompatible_technique_policy is IncompatibleTechniquePolicy.RAISE:
                raise IncompatibleTechniqueError(message)
            logger.warning("Skipping %s", message)
        if single_turn:
            return compatible
        return filter_seed_groups(
            seed_groups=compatible,
            requirements=factory.requirements.seed_group,
            seed_technique=None,
            technique_name=technique.value,
            dataset_name=dataset_name,
            policy=context.incompatible_technique_policy,
            validate_group=factory.validate_group_adversarial_prompts,
        )

    def _build_unit(
        self,
        *,
        context: ScenarioContext,
        technique: AssertTechnique,
        factory: AttackTechniqueFactory,
        group: AttackSeedGroup,
        name: str,
    ) -> AtomicAttack:
        scoring = self._get_scoring(group)
        multi_turn = technique is AssertTechnique.MultiTurn
        attack_technique = factory.create(
            objective_target=context.objective_target,
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=scoring.policy,
                auxiliary_scorers=[scoring.overrefusal],
                use_score_as_feedback=False,
            ),
            seed_group=group if multi_turn else None,
        )
        if not multi_turn:
            # Keep the stored group so single- and multi-turn results share its logical ID and resume.
            # The prompt case doesn't use the group's tester prompts; this records that on the technique.
            attack_technique = AttackTechnique(
                attack=attack_technique.attack,
                requirements=attack_technique.requirements,
                adversarial_prompts=group.adversarial_prompts,
            )
        stance = "permissible" if scoring.permissible else "impermissible"
        return AtomicAttack(
            atomic_attack_name=name,
            display_group=f"{technique.value}_{stance}",
            technique_name=technique.value,
            attack_technique=attack_technique,
            seed_groups=[group],
            adversarial_chat=factory.resolve_adversarial_chat() if multi_turn else None,
            objective_scorer=scoring.policy,
            memory_labels=context.memory_labels,
        )

    def _build_baselines(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Send each group's objective alone, judged by that group's policy.

        The stored prompts already run as ``single_turn``, so the baseline is the plain request.

        Returns:
            list[AtomicAttack]: One baseline unit per selected group.
        """
        baselines = []
        for group in sorted(context.seed_groups, key=self._get_generation_order):
            scoring = self._get_scoring(group)
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_scoring_config=AttackScoringConfig(
                    objective_scorer=scoring.policy, auxiliary_scorers=[scoring.overrefusal]
                ),
            )
            baselines.append(
                AtomicAttack(
                    atomic_attack_name=self._get_unit_name(prefix="baseline", group=group),
                    display_group="baseline",
                    attack_technique=AttackTechnique(
                        attack=attack,
                        requirements=TechniqueRequirements(
                            seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                        ),
                    ),
                    seed_groups=[group],
                    objective_scorer=scoring.policy,
                    memory_labels=context.memory_labels,
                    group_kind=ScenarioRunPlanGroupKind.BASELINE,
                )
            )
        return baselines
