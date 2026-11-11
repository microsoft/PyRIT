# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run stored ASSERT seed groups against a target and judge each conversation against its policy."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import ValidationError

from pyrit.common import apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.converter import TranslationConverter
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import (
    AssertDimensionResult,
    AssertGroupMetadata,
    AssertSkipReason,
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
    SeedMetadataRequirement,
    SeedPrompt,
)
from pyrit.models.scenario_readiness import ScenarioConfigurationIssue, ScenarioReadiness, ScenarioRoleBinding
from pyrit.models.score.assert_judge import AssertJudgeConfig, describe_validation_errors
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import CapabilityName
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario.core import (
    DatasetConstraintError,
    get_default_adversarial_target,
    resolve_technique_factories,
    resolve_technique_factories_for_techniques,
)
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetSourceKind
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    TechniqueRequirements,
    check_target_compatibility,
    describe_target_incompatibility,
    filter_seed_groups,
)
from pyrit.score import (
    AssertJudgmentMode,
    AssertTraceEvidence,
    TraceEvidenceRequirement,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)
from pyrit.score.assert_judge_presets import (
    AssertJudgeDimensionSpec,
    build_assert_judge_scorers,
    resolve_assert_judge_dimensions,
)
from pyrit.score.scorer import _set_snapshot_participation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import (
        ScenarioDatasetSizeEstimate,
        Scorable,
        ScorableUnion,
        Score,
        ScoringExpectation,
        TraceScorable,
    )
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import ObservationSource, Scorer
    from pyrit.score.scorer import SnapshotParticipation

logger = logging.getLogger(__name__)


_ASSERT_PACKAGE = "assert_package"
_ASSERT_PACKAGE_SPANISH = "assert_package_spanish"
_ASSERT_PACKAGES = (_ASSERT_PACKAGE, _ASSERT_PACKAGE_SPANISH)


def _build_assert_package_factory(name: str = _ASSERT_PACKAGE) -> AttackTechniqueFactory:
    """
    Build the scenario-local ``assert_package`` technique: red teaming with ASSERT's own tester.

    It is not registered globally, so only ``foundry.assert_policy`` offers it.

    Returns:
        AttackTechniqueFactory: The technique factory.
    """
    return AttackTechniqueFactory(
        name=name,
        attack_class=RedTeamingAttack,
        description=(
            "Run ASSERT cases: send stored prompt cases directly, or use ASSERT's tester system prompt "
            "with a conversation case's stored tester instructions."
            + (
                " Translate target-bound user requests to Spanish using the configured adversarial chat; "
                "leave comparison baseline and policy instructions unchanged."
                if name == _ASSERT_PACKAGE_SPANISH
                else ""
            )
        ),
        technique_tags=["multi_turn"],
        supports_additional_request_converters=True,
    )


@AttackTechniqueRegistry.cache_scenario_technique_class
def _build_assert_policy_technique() -> type[ScenarioTechnique]:
    """
    Build the technique class from every registered attack technique plus ``assert_package``.

    ``assert_package`` is the default: it sends prompt cases directly and follows conversation
    cases' stored instructions with ASSERT's tester. The optional baseline sends stored prompts.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    factories = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()
    factories.update({name: _build_assert_package_factory(name) for name in _ASSERT_PACKAGES})
    return AttackTechniqueRegistry.build_technique_class_from_factories(
        class_name="AssertPolicyTechnique",
        factories=list(factories.values()),
        default_names={_ASSERT_PACKAGE},
    )


class _AssertPlaceholderScorer(TrueFalseScorer):
    """Scenario identity until the selected groups define their judges; it never scores."""

    SUPPORTED_SCORABLE_TYPES: ClassVar[tuple[type[Scorable], ...]] = (MessageScorable, ConversationScorable)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        raise RuntimeError("AssertPolicy scores each group with its own ASSERT judges, not the placeholder.")


class _AssertJudgedScorer(TrueFalseScorer):
    """
    Judge a conversation only when ASSERT would; otherwise record why no verdict was reached.

    Every score records its dimension with ``AssertDimensionResult``, including skipped conversations.
    """

    SKIP_RATIONALES: ClassVar[dict[AssertSkipReason, str]] = {
        AssertSkipReason.TARGET_INPUT_REFUSED: (
            "A target reply was blocked, so ASSERT does not judge this conversation."
        ),
        AssertSkipReason.TARGET_ERROR: "A target reply was an error, so ASSERT does not judge this conversation.",
    }

    def __init__(self, *, scorer: TrueFalseScorer, dimension_name: str) -> None:
        """
        Wrap one ASSERT conversation judge.

        Args:
            scorer (TrueFalseScorer): The ASSERT conversation judge.
            dimension_name (str): The dimension the judge scores.
        """
        self._scorer = scorer
        self._dimension_name = dimension_name
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"skipped_target_replies": [reason.value for reason in AssertSkipReason]},
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
        return _AssertJudgedScorer(scorer=cast("TrueFalseScorer", scoped), dimension_name=self._dimension_name)

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
                score_category=[self._dimension_name],
                score_metadata=AssertDimensionResult(dimension=self._dimension_name, skip_reason=reason).to_metadata(),
            )
            return [self._anchor(score=skipped, scorable=scorable, child_scores={})]
        scores = await self._scorer._score_nested_async(
            scorable=scorable, expectation=self._scorer._select_expectation(expectation=expectation)
        )
        return [
            self._anchor(score=self._mark_dimension(score), scorable=scorable, child_scores={0: score})
            for score in scores
        ]

    def _mark_dimension(self, score: Score) -> Score:
        wrapped = self._create_wrapper_score(score)
        wrapped.score_category = wrapped.score_category or [self._dimension_name]
        result = AssertDimensionResult.from_metadata(metadata=wrapped.score_metadata) or AssertDimensionResult()
        marked = result.model_copy(update={"dimension": self._dimension_name})
        wrapped.score_metadata = marked.merge_into(wrapped.score_metadata)
        return wrapped

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

    async def _get_skip_reason_async(self, *, conversation_id: str) -> AssertSkipReason | None:
        for piece in await self._memory.get_message_pieces_async(conversation_id=conversation_id):
            if piece.api_role != "assistant" or piece.is_simulated:
                continue
            if piece.is_blocked():
                return AssertSkipReason.TARGET_INPUT_REFUSED
            if piece.has_error() or piece.converted_value_data_type == "error":
                return AssertSkipReason.TARGET_ERROR
        return None


@dataclass(frozen=True)
class _PolicyJudges:
    """The objective judge and auxiliary judges for one policy."""

    objective: TrueFalseScorer
    auxiliaries: tuple[TrueFalseScorer, ...]

    @property
    def all(self) -> tuple[TrueFalseScorer, ...]:
        return (self.objective, *self.auxiliaries)


@dataclass(frozen=True)
class _GroupScoring:
    """The judges and display stance for one stored group."""

    objective: TrueFalseScorer
    auxiliaries: tuple[TrueFalseScorer, ...]
    permissible: bool


class AssertPolicy(Scenario):
    """
    Run stored ASSERT seed groups against a target and judge each conversation against its policy.

    The baseline sends each group's stored system and user prompts once, like an ASSERT prompt
    case. The default technique, ``assert_package``, sends prompt cases directly. Conversation
    cases use a red-teaming tester that follows the group's stored tester instructions for
    ``max_turns`` turns with ASSERT's own tester system prompt. Compatible registered techniques can run too;
    ``red_teaming`` uses the same attack with the group's stored PyRIT tester prompt.

    By default, judges built from each group's stored policy read the whole conversation once:
    policy violation is the objective score, and overrefusal is an auxiliary score. Blocked or
    errored target replies stay undetermined. Results are grouped by each case's generated category,
    across baseline and selected techniques, qualified by policy when several policies are selected.

    SDK callers can supply a configured objective scorer instead of these defaults, which is used
    unchanged. The legacy judge settings (``judge_presets``, ``judge_preset_files``,
    ``disabled_judge_dimensions``, ``objective_dimension``, ``judge_samples``, ``judgment_mode`` and
    ``target_system_prompt``) still configure the default judges through the SDK.

    With a trace source, which is set through the SDK or CLI, each default judge also reads the
    execution traces the target recorded for the conversation (tool calls with arguments and
    results, model calls, and agent steps). ``trace_requirement`` sets the evidence a judge needs
    before it runs; the default judges every conversation, as ASSERT does, with whatever traces
    are available. A supplied objective scorer owns its own evidence configuration.

    ``assert_package_spanish`` translates target-bound user requests with the configured adversarial
    chat, including later conversation turns. It leaves the optional comparison baseline unchanged.
    """

    #: Version 2 runs registered attack techniques instead of ``single_turn``/``multi_turn``.
    VERSION: int = 2
    #: ASSERT's tester prompts. The system prompt is ASSERT's text, and the first message and reply
    #: framing match ASSERT's tester loop, which shows each reply in ``<target_response>`` tags.
    TESTER_SYSTEM_PROMPT_PATH: ClassVar[Path] = EXECUTOR_RED_TEAM_PATH / "assert_tester_system.yaml"
    TESTER_FIRST_MESSAGE: ClassVar[str] = "Begin the conversation now with the user's first message only."
    TESTER_FEEDBACK_TEMPLATE: ClassVar[str] = "<target_response>\n{{ feedback_text }}\n</target_response>"
    _JUDGE_PARAMETER_NAMES: ClassVar[tuple[str, ...]] = (
        "judge_presets",
        "judge_preset_files",
        "disabled_judge_dimensions",
        "judge_samples",
        "objective_dimension",
        "trace_source",
        "trace_requirement",
    )
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        judge_target: PromptTarget | None = None,
        trace_source: ObservationSource[TraceScorable] | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the scenario.

        Args:
            adversarial_chat (PromptTarget | None): The adversarial chat for techniques that use one and
                don't set their own. Defaults to the registered adversarial target.
            objective_scorer (TrueFalseScorer | None): A scorer used unchanged for every group. When
                omitted, each stored policy defines the default policy-violation and overrefusal judges.
                Configure a supplied scorer on the scorer itself, not with the legacy judge settings.
            judge_target (PromptTarget | None): The ASSERT judge. Defaults to the registered scorer
                target. The explicit ``scoring_target`` run parameter takes precedence.
            trace_source (ObservationSource[TraceScorable] | None): Execution evidence for the
                judges, such as ``LocalTraceCapture.source()``. A registered source can instead be
                named with the ``trace_source`` parameter. Defaults to None.
            scenario_result_id (str | None): Existing result to resume.

        Raises:
            TypeError: If the supplied objective scorer is not a true/false scorer.
            ValueError: If a supplied objective scorer is combined with a judge target.
        """
        if objective_scorer is not None and not isinstance(objective_scorer, TrueFalseScorer):
            raise TypeError("objective_scorer must be a TrueFalseScorer.")
        if objective_scorer is not None and (judge_target is not None or trace_source is not None):
            raise ValueError(
                "A supplied objective_scorer owns its judge target and trace configuration; "
                "omit judge_target and trace_source."
            )
        self._assert_objective_scorer = objective_scorer
        self._adversarial_chat = adversarial_chat
        self._judge_target = judge_target
        self._trace_source = trace_source
        self._judges_by_policy: dict[str, _PolicyJudges] = {}
        self._judge_dimensions: tuple[AssertJudgeDimensionSpec, ...] = ()
        self._repeated_ids: set[str] = set()
        super().__init__(
            version=self.VERSION,
            technique_class=_build_assert_policy_technique(),
            default_dataset_config=DatasetAttackConfiguration(max_per_dataset="all", max_total="all"),
            objective_scorer=objective_scorer if objective_scorer is not None else _AssertPlaceholderScorer(),
            scenario_result_id=scenario_result_id,
            uses_default_adversarial_target=adversarial_chat is None,
        )

    def get_readiness(self) -> ScenarioReadiness:
        """
        Resolve ASSERT's judge target before its dataset-specific judges are built.

        A supplied objective scorer owns its own target, so no judge target is resolved for it.

        Returns:
            ScenarioReadiness: Execution-role bindings and any missing judge target.
        """
        readiness = super().get_readiness()
        if self._assert_objective_scorer is not None:
            return readiness
        try:
            target = self._resolve_scoring_target(chat_target=self._judge_target)
        except ValueError as error:
            readiness.issues.append(
                ScenarioConfigurationIssue(
                    role="scoring_target",
                    component=self.name,
                    reason=str(error),
                    remediation="Select a scoring target, or register objective_scorer_chat for ASSERT's judges.",
                )
            )
        else:
            readiness.bindings = [binding for binding in readiness.bindings if binding.role != "scoring_target"]
            readiness.bindings.append(
                ScenarioRoleBinding(role="scoring_target", component=self.name, identifier=target.get_identifier())
            )
        return readiness

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the ASSERT run settings.

        Returns:
            list[Parameter]: Turn count, judgment mode, target-owned instructions, and judge dimensions.
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
            Parameter(
                name="judge_presets",
                description=(
                    "Built-in judge presets, such as safety-extended or tool-use, whose dimensions are added to "
                    "the built-in policy_violation and overrefusal. Later presets replace earlier dimensions "
                    "with the same name."
                ),
                param_type=list[str],
                default=None,
            ),
            Parameter(
                name="judge_preset_files",
                description=(
                    "Your own judge_preset YAML files, applied after judge_presets. Python callers only; the "
                    "server API doesn't read files named by its callers."
                ),
                param_type=list[Path],
                default=None,
            ),
            Parameter(
                name="disabled_judge_dimensions",
                description="Judge dimensions to remove after presets are applied, including built-ins.",
                param_type=list[str],
                default=None,
            ),
            Parameter(
                name="judge_samples",
                description=(
                    "Independent judge calls per dimension, combined by majority vote with ties counted as "
                    "failures, as in ASSERT. Prefer an odd number."
                ),
                param_type=int,
                default=1,
            ),
            Parameter(
                name="objective_dimension",
                description="The enabled judge dimension used as the objective score.",
                param_type=str,
                default=AssertJudgeConfig.DEFAULT_OBJECTIVE_DIMENSION,
            ),
            Parameter(
                name="trace_source",
                description=(
                    "Registered trace source whose execution evidence the judges read with the transcript. "
                    "The target must record a trace for each request."
                ),
                param_type=str | None,
                default=None,
            ),
            Parameter(
                name="trace_requirement",
                description=(
                    "Trace evidence a judge needs before it runs: none always judges, with whatever traces "
                    "exist; available needs some trace; complete needs complete traces. Otherwise the result "
                    "is undetermined."
                ),
                param_type=TraceEvidenceRequirement,
                default=TraceEvidenceRequirement.NONE,
            ),
        ]

    @classmethod
    def _common_scenario_parameter_names(cls) -> frozenset[str]:
        """
        Exclude judge settings from the generic identity parameters; ``_get_identity_params`` adds them.

        Returns:
            frozenset[str]: The common parameter names plus the judge settings.
        """
        return super()._common_scenario_parameter_names() | frozenset(cls._JUDGE_PARAMETER_NAMES)

    def _get_identity_params(self) -> dict[str, Any]:
        """
        Record judge and trace settings that differ from the defaults.

        Default settings add nothing, so runs from before judge settings existed still resume.
        Preset files are not recorded: they can't be restored through the API, and every resolved
        judge, including its dimension content, is already part of the identity. A registered
        trace source is recorded by name, which is stable to replay; a source passed to the
        constructor is only identified through the judges.

        Returns:
            dict[str, Any]: The scenario parameters plus non-default judge and trace settings.
        """
        params = super()._get_identity_params()
        config = self._get_judge_config()
        defaults = AssertJudgeConfig()
        if config.presets:
            params["judge_presets"] = list(config.presets)
        if config.disabled_dimensions:
            params["disabled_judge_dimensions"] = list(config.disabled_dimensions)
        if config.samples != defaults.samples:
            params["judge_samples"] = config.samples
        if config.objective_dimension != defaults.objective_dimension:
            params["objective_dimension"] = config.objective_dimension
        if self.params.get("trace_source"):
            params["trace_source"] = self.params["trace_source"]
        requirement = self._get_trace_requirement()
        if requirement is not TraceEvidenceRequirement.NONE:
            params["trace_requirement"] = requirement.value
        return params

    def _validate_runtime_configuration(self) -> None:
        super()._validate_runtime_configuration()
        if self.params["max_turns"] < 1:
            raise ValueError("max_turns must be a positive integer.")
        prompt = self.params.get("target_system_prompt")
        if prompt is not None and not prompt.strip():
            raise ValueError("target_system_prompt must not be blank.")
        if self._trace_source is not None and self.params.get("trace_source"):
            raise ValueError("Pass a trace source either to the constructor or as the trace_source parameter.")
        config = self._get_judge_config()
        trace_requirement = self._get_trace_requirement()
        if self._assert_objective_scorer is None:
            if (
                trace_requirement is not TraceEvidenceRequirement.NONE
                and self._trace_source is None
                and not self.params.get("trace_source")
            ):
                raise ValueError("trace_requirement needs a trace source to acquire execution evidence.")
            return
        judgment_mode = AssertJudgmentMode(self.params["judgment_mode"])
        if (
            config != AssertJudgeConfig()
            or judgment_mode is not AssertJudgmentMode.CATEGORY_VALIDATED
            or prompt
            or self.params.get("trace_source")
            or trace_requirement is not TraceEvidenceRequirement.NONE
        ):
            raise ValueError(
                "A supplied objective_scorer owns its scoring configuration; omit non-default ASSERT judge settings."
            )
        selected_target = self.params.get("scoring_target")
        if selected_target is not None and selected_target is not self._assert_objective_scorer.get_chat_target():
            raise ValueError("Configure the scoring target on the supplied objective_scorer, not on the scenario.")

    async def _get_run_size_estimate_async(self, *, target_is_configured: bool = False) -> ScenarioRunSizeEstimate:
        estimate = await super()._get_run_size_estimate_async(target_is_configured=target_is_configured)
        # Bounded estimates resolved the presets already; unbounded ones still report invalid presets.
        if estimate.status is ScenarioRunSizeEstimateStatus.Unavailable:
            await self._resolve_judge_dimensions_async()
        return estimate

    async def _resolve_judge_dimensions_async(self) -> None:
        """Read the selected judge presets in a worker thread, so the event loop isn't blocked."""
        if self._assert_objective_scorer is not None:
            self._judge_dimensions = ()
            return
        self._judge_dimensions = await asyncio.to_thread(resolve_assert_judge_dimensions, self._get_judge_config())

    def _get_judge_config(self) -> AssertJudgeConfig:
        try:
            return AssertJudgeConfig(
                presets=tuple(self.params.get("judge_presets") or ()),
                preset_files=tuple(Path(path) for path in self.params.get("judge_preset_files") or ()),
                disabled_dimensions=tuple(self.params.get("disabled_judge_dimensions") or ()),
                samples=self.params["judge_samples"],
                objective_dimension=self.params["objective_dimension"],
            )
        except ValidationError as error:
            raise ValueError(f"Invalid ASSERT judge settings: {describe_validation_errors(error)}") from None

    def _resolve_trace_evidence(self) -> AssertTraceEvidence | None:
        """
        Resolve the configured trace source into the judges' trace evidence.

        Returns:
            AssertTraceEvidence | None: The evidence configuration, or None without a trace source.

        Raises:
            ValueError: If the named trace source is not registered.
        """
        source = self._trace_source
        name = self.params.get("trace_source")
        if source is None and name:
            from pyrit.registry import SourceRegistry

            source = SourceRegistry.get_registry_singleton().instances.get(name)
            if source is None:
                raise ValueError(
                    f"Trace source '{name}' is not registered. Register one with LocalTraceCapture.register() "
                    "or SourceRegistry before running foundry.assert_policy."
                )
        if source is None:
            return None
        return AssertTraceEvidence(source=source, requirement=self._get_trace_requirement())

    def _get_trace_requirement(self) -> TraceEvidenceRequirement:
        return TraceEvidenceRequirement(self.params.get("trace_requirement") or TraceEvidenceRequirement.NONE)

    def _has_no_datasets(self) -> bool:
        return self._dataset_config.source_kind is DatasetSourceKind.MEMORY and not self._dataset_config.dataset_names

    def _get_run_size_budget(self) -> ScenarioDatasetSizeEstimate:
        return BoundedDatasetSize(value=1) if self._has_no_datasets() else super()._get_run_size_budget()

    async def _estimate_run_size_async(self, *, budget: BoundedDatasetSize) -> ScenarioRunSizeEstimate:
        await self._resolve_judge_dimensions_async()
        count, datasets = await self._get_dataset_size_for_estimate_async(budget=budget)
        components = [
            ScenarioRunSizeComponent(label=f"{technique.value} units", count=count)
            for technique in self._scenario_techniques
        ]
        if self._include_baseline:
            components.append(ScenarioRunSizeComponent(label="Baseline", count=count, is_baseline=True))
        scoring_note = (
            "Each conversation uses the supplied objective scorer"
            if self._assert_objective_scorer is not None
            else f"Each conversation gets {self._count_judge_calls()} judge calls"
        )
        note = (
            "At most one unit per selected group for each technique; groups a technique can't use are skipped. "
            f"{scoring_note}, and techniques that take a turn count make up to {self.params['max_turns']} "
            "target turns."
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

    def _count_judge_calls(self) -> int:
        samples: int = self.params["judge_samples"]
        return len(self._judge_dimensions) * samples

    def get_dataset_requirements(self) -> SeedGroupRequirements:
        """
        Declare the policy context required by every ASSERT judge, regardless of provenance.

        Returns:
            SeedGroupRequirements: The scenario-wide ASSERT schema check.
        """
        return SeedGroupRequirements(
            metadata=(
                SeedMetadataRequirement(
                    name="assert_generation",
                    key=AssertGroupMetadata.METADATA_KEY,
                    description="ASSERT generation metadata and policy taxonomy",
                    validator=AssertGroupMetadata.from_metadata,
                ),
            )
        )

    def get_baseline_dataset_requirements(self) -> SeedGroupRequirements:
        """
        Declare the stored user prompt required by ASSERT's prompt-case baseline.

        Returns:
            SeedGroupRequirements: The prompt-case baseline's input requirements.
        """
        return SeedGroupRequirements(required_user_prompt=True)

    def _get_dataset_technique_factories(self) -> dict[str, AttackTechniqueFactory]:
        return resolve_technique_factories_for_techniques(
            scenario_techniques=self._scenario_techniques,
            extra_factories={name: _build_assert_package_factory(name) for name in _ASSERT_PACKAGES},
        )

    def _get_dataset_target_errors(self, factory: AttackTechniqueFactory) -> list[str]:
        # ASSERT's normal recipe chooses its existing attack from the case data below.
        if factory.name in _ASSERT_PACKAGES or self._objective_target is None:
            return []
        return self._get_target_requirements(factory).check_target(
            target=self._objective_target, attack_class=factory.attack_class
        )

    def _validate_dataset_technique_group(
        self, *, factory: AttackTechniqueFactory, seed_group: AttackSeedGroup
    ) -> None:
        if factory.name in _ASSERT_PACKAGES:
            self._validate_assert_package_group(factory=factory, group=seed_group, target=self._objective_target)
        else:
            super()._validate_dataset_technique_group(factory=factory, seed_group=seed_group)

    def _validate_dataset_population(self, population: dict[str, list[AttackSeedGroup]]) -> None:
        super()._validate_dataset_population(population)
        self._validate_assert_cases(population)

    def _validate_assert_cases(self, population: dict[str, list[AttackSeedGroup]]) -> None:
        for name, groups in population.items():
            for group in groups:
                try:
                    self._validate_assert_case(group)
                except IncompatibleTechniqueError as error:
                    raise DatasetConstraintError(
                        f"Dataset '{name}' contains an invalid ASSERT case: {error}"
                    ) from error

    @classmethod
    def _validate_assert_case(cls, group: AttackSeedGroup) -> None:
        if group.adversarial_prompts is not None:
            cls._require_tester_instructions(group)
            return
        errors = SeedGroupRequirements(required_user_prompt=True).check(seed_group=group)
        if errors:
            raise IncompatibleTechniqueError("An ASSERT prompt case requires a stored user prompt.")

    def _validate_assert_package_group(
        self, *, factory: AttackTechniqueFactory, group: AttackSeedGroup, target: PromptTarget | None
    ) -> None:
        self._validate_assert_case(group)
        if target is not None:
            delivery = self._get_assert_case_factory(factory=factory, group=group)
            errors = self._get_target_requirements(delivery).check_target(
                target=target, attack_class=delivery.attack_class
            )
            if errors:
                raise IncompatibleTechniqueError(
                    describe_target_incompatibility(technique_name=factory.name, errors=errors)
                )

    @staticmethod
    def _get_assert_case_factory(*, factory: AttackTechniqueFactory, group: AttackSeedGroup) -> AttackTechniqueFactory:
        if group.adversarial_prompts is not None:
            return factory
        return AttackTechniqueFactory(
            name=factory.name, attack_class=PromptSendingAttack, supports_additional_request_converters=True
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
        await self._resolve_judge_dimensions_async()
        selection = await self._dataset_config._resolve_attack_groups_async()
        population = selection.select(apply_sampling=False)
        self._validate_dataset_population(population)
        if not any(population.values()):
            raise DatasetConstraintError("The selected ASSERT datasets contain no seed groups.")
        groups = [group for dataset_groups in population.values() for group in dataset_groups]
        self._validate_dataset_assessment_population(population)
        selected = (
            self._sample_dataset_population(population=population, selection=selection)
            if apply_sampling
            else population
        )
        await asyncio.to_thread(self._configure_judges, population=population)
        counts = Counter(group.logical_id for group in groups)
        self._repeated_ids = {logical_id for logical_id, count in counts.items() if count > 1}
        return selected

    def _validate_dataset_assessment_population(self, population: dict[str, list[AttackSeedGroup]]) -> None:
        self._validate_assert_cases(population)
        self._resolve_trace_evidence()
        groups = [group for dataset_groups in population.values() for group in dataset_groups]
        repeated_seeds = [seed_id for seed_id, count in Counter(g.objective.id for g in groups).items() if count > 1]
        if repeated_seeds:
            raise ValueError(
                f"The selected ASSERT seed groups include objective seed {repeated_seeds[0]} more than once. "
                "Select each stored group once."
            )
        if self.params.get("target_system_prompt") is not None and any(
            self._get_stored_system_prompt(group) for group in groups
        ):
            raise ValueError(
                "target_system_prompt is only for target-owned instructions, but a selected group stores "
                "its own system prompt. Remove target_system_prompt, or generate the groups with "
                "target_owns_instructions=True."
            )

    def _configure_judges(self, *, population: dict[str, list[AttackSeedGroup]]) -> None:
        """
        Build one judge set per policy and replace the placeholder identity with all of them.

        Raises:
            ValueError: If a group lacks ASSERT metadata, or stores a system prompt while
                ``target_system_prompt`` is set.
        """
        if self._assert_objective_scorer is not None:
            self._judges_by_policy = {
                self._get_policy_key(
                    metadata=self._read_metadata(group=group, dataset_name=name),
                    system_prompt=self._get_judged_system_prompt(group),
                ): _PolicyJudges(objective=self._assert_objective_scorer, auxiliaries=())
                for name, groups in population.items()
                for group in groups
            }
            self._objective_scorer = self._assert_objective_scorer
            return
        judge_target = self._resolve_scoring_target(chat_target=self._judge_target)
        mode = AssertJudgmentMode(self.params["judgment_mode"])
        trace_evidence = self._resolve_trace_evidence()
        self._judges_by_policy = {}
        for dataset_name, groups in population.items():
            for group in groups:
                metadata = self._read_metadata(group=group, dataset_name=dataset_name)
                system_prompt = self._get_judged_system_prompt(group)
                key = self._get_policy_key(metadata=metadata, system_prompt=system_prompt)
                if key not in self._judges_by_policy:
                    self._judges_by_policy[key] = self._build_judges(
                        judge_target=judge_target,
                        metadata=metadata,
                        system_prompt=system_prompt,
                        mode=mode,
                        trace_evidence=trace_evidence,
                    )
        # The OR composite's identity doesn't depend on judge order. The objective dimension is part of
        # the identity parameters.
        self._objective_scorer = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.OR,
            scorers=[judge for judges in self._judges_by_policy.values() for judge in judges.all],
        )
        self._objective_scorer_identifier = self._objective_scorer.get_identifier()

    def _get_scoring(self, group: AttackSeedGroup) -> _GroupScoring:
        metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
        key = self._get_policy_key(metadata=metadata, system_prompt=self._get_judged_system_prompt(group))
        judges = self._judges_by_policy[key]
        category = next(
            item for item in metadata.taxonomy.behavior_categories if item.name == metadata.behavior_category
        )
        return _GroupScoring(
            objective=judges.objective, auxiliaries=judges.auxiliaries, permissible=category.permissible
        )

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
                f"('{group.objective.value[:80]}'). Generate or import groups that meet the ASSERT schema. "
                f"Details: {error}"
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

    def _build_judges(
        self,
        *,
        judge_target: PromptTarget,
        metadata: AssertGroupMetadata,
        system_prompt: str | None,
        mode: AssertJudgmentMode,
        trace_evidence: AssertTraceEvidence | None = None,
    ) -> _PolicyJudges:
        """
        Build one judge per enabled dimension for one policy.

        Returns:
            _PolicyJudges: The objective judge and the auxiliary judges, in resolved order.
        """
        config = self._get_judge_config()
        scorers = build_assert_judge_scorers(
            target=judge_target,
            taxonomy=metadata.taxonomy,
            config=config,
            dimensions=self._judge_dimensions,
            application_context=metadata.config.context,
            target_system_prompt=system_prompt,
            judgment_mode=mode,
            trace_evidence=trace_evidence,
        )
        judged = {name: _AssertJudgedScorer(scorer=scorer, dimension_name=name) for name, scorer in scorers.items()}
        return _PolicyJudges(
            objective=judged[config.objective_dimension],
            auxiliaries=tuple(scorer for name, scorer in judged.items() if name != config.objective_dimension),
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        self._warn_if_target_untraced(target=context.objective_target)
        local = {name: _build_assert_package_factory(name) for name in _ASSERT_PACKAGES}
        factories = resolve_technique_factories(context=context, extra_factories=local)
        display_groups = self._get_display_groups(context=context, technique_names=list(factories))
        attacks = (
            self._build_baselines(context=context, display_groups=display_groups) if context.include_baseline else []
        )
        for name, factory in factories.items():
            if name not in _ASSERT_PACKAGES and not check_target_compatibility(
                target=context.objective_target,
                attack_class=factory.attack_class,
                requirements=self._get_target_requirements(factory),
                technique_name=name,
                policy=context.incompatible_technique_policy,
            ):
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
                    validate_group=lambda group, factory=factory: (
                        self._validate_assert_package_group(
                            factory=factory, group=group, target=context.objective_target
                        )
                        if factory.name in _ASSERT_PACKAGES
                        else self._validate_dataset_technique_group(factory=factory, seed_group=group)
                    ),
                )
                for group in sorted(selected, key=self._get_generation_order):
                    attacks.append(
                        await asyncio.to_thread(
                            self._build_unit,
                            context=context,
                            technique_name=name,
                            factory=factory,
                            group=group,
                            display_group=display_groups[self._get_unit_name(prefix=name, group=group)],
                        )
                    )
        return attacks

    def _get_display_groups(self, *, context: ScenarioContext, technique_names: list[str]) -> dict[str, str]:
        names = [
            self._get_unit_name(prefix=technique_name, group=group)
            for group in context.seed_groups
            for technique_name in ["baseline", *technique_names]
        ]
        if context.stored_run_plan is not None:
            recorded = {
                group.atomic_attack_name: group.display_group for group in context.stored_run_plan.atomic_groups
            }
            return {name: recorded.get(name, name) for name in names}
        if context.scenario_result_id == self._scenario_result_id and self._scenario_result_id is not None:
            return {name: context.stored_display_group_map.get(name, name) for name in names}
        assignments: dict[str, tuple[str, str]] = {}
        policies: dict[str, str] = {}
        for group in context.seed_groups:
            metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
            policy = json.dumps(
                {
                    "taxonomy": metadata.taxonomy.model_dump(mode="json"),
                    "behavior_description": metadata.config.behavior_description,
                    "context": metadata.config.context,
                    "effective_system_prompt": self._get_judged_system_prompt(group),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            policies[policy] = metadata.taxonomy.behavior.name
            assignments[str(group.objective.id)] = (policy, metadata.behavior_category)

        policies_by_name: dict[str, list[str]] = {}
        for policy, name in policies.items():
            policies_by_name.setdefault(name, []).append(policy)
        policy_positions = {
            policy: position
            for named_policies in policies_by_name.values()
            for position, policy in enumerate(sorted(named_policies), start=1)
        }

        display_groups: dict[str, str] = {}
        for group in context.seed_groups:
            policy, category = assignments[str(group.objective.id)]
            category_label = category
            if len(policies) > 1:
                policy_name = policies[policy]
                category_label = (
                    f"{json.dumps(category, ensure_ascii=False)} / {json.dumps(policy_name, ensure_ascii=False)}"
                )
                if len(policies_by_name[policy_name]) > 1:
                    category_label += f" (policy {policy_positions[policy]})"
            for technique_name in ["baseline", *technique_names]:
                display_groups[self._get_unit_name(prefix=technique_name, group=group)] = category_label
        return display_groups

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

    def _warn_if_target_untraced(self, *, target: PromptTarget) -> None:
        """
        Warn, as ASSERT does, when judges expect traces that the target will not record.

        Tracing belongs to the target, so the scenario does not change it.
        """
        if self._trace_source is None and not self.params.get("trace_source"):
            return
        if target.trace_config.enabled:
            return
        consequence = (
            "judges will use the transcript alone"
            if self._get_trace_requirement() is TraceEvidenceRequirement.NONE
            else "results will be undetermined"
        )
        logger.warning(
            "foundry.assert_policy has a trace source, but the objective target does not record request traces, "
            "so %s. Give the target trace_config=TargetTraceConfig(enabled=True) "
            "(for example LocalTraceCapture.trace_config()).",
            consequence,
        )

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
        display_group: str,
    ) -> AtomicAttack:
        prompt_case = technique_name in _ASSERT_PACKAGES and group.adversarial_prompts is None
        if technique_name in _ASSERT_PACKAGES:
            factory = self._get_assert_case_factory(factory=factory, group=group)
        scoring = self._get_scoring(group)
        extra = list(context.technique_converters.get(technique_name) or [])
        if technique_name == _ASSERT_PACKAGE_SPANISH:
            extra.append(
                TranslationConverter(
                    converter_target=self._adversarial_chat or get_default_adversarial_target(), language="Spanish"
                )
            )
        adversarial_chat = (
            self._adversarial_chat if factory.uses_adversarial and factory.adversarial_chat is None else None
        )
        create_kwargs: dict[str, Any] = {
            "objective_target": context.objective_target,
            "attack_scoring_config": AttackScoringConfig(
                objective_scorer=scoring.objective,
                auxiliary_scorers=list(scoring.auxiliaries),
                use_score_as_feedback=False,
            ),
            "adversarial_chat": adversarial_chat,
            "extra_request_converters": ConverterConfiguration.from_converters(converters=extra) if extra else None,
        }
        if technique_name in _ASSERT_PACKAGES and not prompt_case:
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
            display_group=display_group,
            technique_name=technique_name,
            attack_technique=attack_technique,
            seed_groups=[group],
            adversarial_chat=adversarial_chat or factory.resolve_adversarial_chat(),
            objective_scorer=scoring.objective,
            memory_labels=context.memory_labels,
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
                "The group stores no tester instructions; generate groups with include_adversarial_prompt=True."
            )

    @staticmethod
    def _get_tester_instructions(group: AttackSeedGroup) -> str | None:
        prompts = group.adversarial_prompts
        description = prompts.system_prompt.description if prompts and prompts.system_prompt else None
        return description.strip() if description and description.strip() else None

    def _build_baselines(self, *, context: ScenarioContext, display_groups: dict[str, str]) -> list[AtomicAttack]:
        """
        Send each group's stored system and user prompts once, judged by that group's judges.

        This is ASSERT's prompt case. Groups without a stored user prompt are skipped.

        Returns:
            list[AtomicAttack]: One baseline unit per group with a stored user prompt.
        """
        prompted = sorted(
            filter_seed_groups(
                seed_groups=context.seed_groups,
                requirements=self.get_baseline_dataset_requirements(),
                seed_technique=None,
                technique_name="baseline",
                dataset_name="selected datasets",
                policy=context.incompatible_technique_policy,
            ),
            key=self._get_generation_order,
        )
        baselines = []
        for group in prompted:
            scoring = self._get_scoring(group)
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_scoring_config=AttackScoringConfig(
                    objective_scorer=scoring.objective, auxiliary_scorers=list(scoring.auxiliaries)
                ),
            )
            baselines.append(
                AtomicAttack(
                    atomic_attack_name=self._get_unit_name(prefix="baseline", group=group),
                    display_group=display_groups[self._get_unit_name(prefix="baseline", group=group)],
                    # Keep the stored group so baseline and technique results share its logical ID and resume.
                    # The prompt case doesn't use the group's tester prompts; this records that on the technique.
                    attack_technique=AttackTechnique(attack=attack, adversarial_prompts=group.adversarial_prompts),
                    seed_groups=[group],
                    objective_scorer=scoring.objective,
                    memory_labels=context.memory_labels,
                    group_kind=ScenarioRunPlanGroupKind.BASELINE,
                )
            )
        return baselines
