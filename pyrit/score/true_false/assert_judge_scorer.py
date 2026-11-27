# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Judge conversations against the ASSERT policy that each expectation carries."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.models import (
    AssertDimensionResult,
    AssertJudgeDimension,
    AssertSkipReason,
    ComponentIdentifier,
    ConversationScorable,
    MessageScorable,
    ViolatesAssertPolicy,
)
from pyrit.score.evidence.conversation_trace_evidence import TraceEvidenceRequirement
from pyrit.score.scorer import _set_snapshot_participation
from pyrit.score.true_false.assert_policy_scorer import (
    AssertJudgmentMode,
    AssertScoringDimension,
    build_assert_policy_scorer,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import Scorable, ScorableUnion, Score, ScoringExpectation, TraceScorable
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.observation.observation_source import ObservationSource
    from pyrit.score.scorer import Scorer, SnapshotParticipation


class AssertJudgeScorer(TrueFalseScorer):
    """
    Judge one ASSERT dimension against the policy in the expectation's ``ViolatesAssertPolicy``.

    True means the dimension's failure occurred. The policy comes from the expectation, so one
    scorer judges every ASSERT dataset. As in ASSERT, a conversation with a blocked or errored
    target reply is not judged, and a blocked judge reply is undetermined.
    """

    CONDITION_TYPE = ViolatesAssertPolicy
    SUPPORTED_SCORABLE_TYPES: ClassVar[tuple[type[Scorable], ...]] = (MessageScorable, ConversationScorable)
    _SKIP_RATIONALES: ClassVar[dict[AssertSkipReason, str]] = {
        AssertSkipReason.TARGET_INPUT_REFUSED: (
            "A target reply was blocked, so ASSERT does not judge this conversation."
        ),
        AssertSkipReason.TARGET_ERROR: "A target reply was an error, so ASSERT does not judge this conversation.",
    }

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        dimension: AssertScoringDimension | AssertJudgeDimension | str = AssertScoringDimension.POLICY_VIOLATION,
        judgment_mode: AssertJudgmentMode = AssertJudgmentMode.CATEGORY_VALIDATED,
        samples: int = 1,
        trace_source: ObservationSource[TraceScorable] | None = None,
        trace_requirement: TraceEvidenceRequirement = TraceEvidenceRequirement.NONE,
    ) -> None:
        """
        Configure the judge once; each scored conversation supplies its own policy.

        Args:
            chat_target (PromptTarget): The judge target, not the application under evaluation.
            dimension (AssertScoringDimension | AssertJudgeDimension | str): A built-in failure, or a
                custom dimension such as one from a judge preset. Defaults to policy violation.
            judgment_mode (AssertJudgmentMode): How a built-in dimension is judged. Defaults to
                CATEGORY_VALIDATED.
            samples (int): Independent judge calls, combined by ASSERT's vote with ties counted as
                failures. Defaults to 1.
            trace_source (ObservationSource[TraceScorable] | None): Execution traces the judge reads
                with the transcript, such as ``LocalTraceCapture.source()``. Defaults to None.
            trace_requirement (TraceEvidenceRequirement): Trace evidence required before the judge runs;
                otherwise the result is undetermined. Defaults to NONE, which always judges.

        Raises:
            ValueError: If the sample count is not positive, or a trace requirement has no source.
        """
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
            raise ValueError("samples must be a positive integer.")
        requirement = TraceEvidenceRequirement(trace_requirement)
        if trace_source is None and requirement is not TraceEvidenceRequirement.NONE:
            raise ValueError("A trace requirement needs a trace_source to acquire execution evidence.")
        self._dimension = (
            dimension if isinstance(dimension, AssertJudgeDimension) else AssertScoringDimension(dimension)
        )
        self._chat_target = chat_target
        self._judgment_mode = AssertJudgmentMode(judgment_mode)
        self._samples = samples
        self._trace_source = trace_source
        self._trace_requirement = requirement
        self._judges_by_policy: dict[str, TrueFalseScorer] = {}
        super().__init__()

    @property
    def dimension_name(self) -> str:
        """The score category this judge writes."""
        dimension = self._dimension
        return dimension.name if isinstance(dimension, AssertJudgeDimension) else dimension.value

    def get_chat_target(self) -> PromptTarget:
        """
        Return the judge target.

        Returns:
            PromptTarget: The target the judge calls.
        """
        return self._chat_target

    def _build_identifier(self) -> ComponentIdentifier:
        dimension = self._dimension
        return self._create_identifier(
            params={
                "dimension": dimension.model_dump(mode="json")
                if isinstance(dimension, AssertJudgeDimension)
                else dimension.value,
                "judgment_mode": self._judgment_mode.value,
                "samples": self._samples,
                "trace_requirement": self._trace_requirement.value,
                "skipped_target_replies": [reason.value for reason in AssertSkipReason],
            },
            prompt_target=self._chat_target.get_identifier(),
            children={"trace_source": self._trace_source.get_identifier()} if self._trace_source else None,
        )

    def _get_judge(self, policy: ViolatesAssertPolicy) -> TrueFalseScorer:
        """
        Build the judge for a policy once, then reuse it.

        Returns:
            TrueFalseScorer: The policy's judge.
        """
        key = policy.model_dump_json()
        if key not in self._judges_by_policy:
            judge = build_assert_policy_scorer(
                target=self._chat_target,
                taxonomy=policy.taxonomy,
                dimension=self._dimension,
                application_context=policy.application_context,
                target_system_prompt=policy.target_instructions,
                judgment_mode=self._judgment_mode,
                samples=self._samples,
                trace_source=self._trace_source,
                trace_requirement=self._trace_requirement,
            )
            self._judges_by_policy[key] = judge.with_scorer_block_policy(raise_if_scorer_blocks=False)
        return self._judges_by_policy[key]

    def _get_child_expectations(
        self, *, expectation: ScoringExpectation | None
    ) -> tuple[tuple[Scorer, ScoringExpectation | None], ...]:
        """
        Pair the expectation's policy judge with its input.

        Returns:
            tuple: The policy's judge and its expectation, or nothing without a policy.
        """
        conditions = expectation.conditions if expectation else ()
        policy = next((condition for condition in conditions if isinstance(condition, ViolatesAssertPolicy)), None)
        if policy is None:
            return ()
        judge = self._get_judge(policy)
        return ((judge, judge._select_expectation(expectation=expectation)),)

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
        policy = self._get_required_condition(expectation=expectation, condition_type=ViolatesAssertPolicy)
        judge = self._get_judge(policy)
        reason = await self._get_skip_reason_async(scorable=scorable)
        if reason is not None:
            skipped = self._build_undetermined_score(
                rationale=self._SKIP_RATIONALES[reason],
                description="ASSERT does not judge this conversation.",
                objective=expectation.objective if expectation else None,
                score_category=[self.dimension_name],
                score_metadata=AssertDimensionResult(dimension=self.dimension_name, skip_reason=reason).to_metadata(),
            )
            return [self._anchor(score=skipped, scorable=scorable, child_scores={})]
        scores = await judge._score_nested_async(
            scorable=scorable, expectation=judge._select_expectation(expectation=expectation)
        )
        return [
            self._anchor(score=self._mark_dimension(score), scorable=scorable, child_scores={0: score})
            for score in scores
        ]

    def _mark_dimension(self, score: Score) -> Score:
        marked = self._create_wrapper_score(score)
        marked.score_category = [self.dimension_name]
        result = AssertDimensionResult.from_metadata(metadata=marked.score_metadata) or AssertDimensionResult()
        marked.score_metadata = result.model_copy(update={"dimension": self.dimension_name}).merge_into(
            marked.score_metadata
        )
        return marked

    def _anchor(self, *, score: Score, scorable: Scorable, child_scores: dict[int, Score]) -> Score:
        score.scorable = cast("ScorableUnion", scorable)
        score.message_piece_id = self._piece_id_from_scorable(scorable)
        _set_snapshot_participation(score=score, child_scores=child_scores)
        return score

    async def _get_skip_reason_async(self, *, scorable: Scorable) -> AssertSkipReason | None:
        """
        Find the target reply that keeps ASSERT from judging this conversation.

        Returns:
            AssertSkipReason | None: Why the conversation is not judged, or None to judge it.
        """
        if isinstance(scorable, MessageScorable):
            scored = await self._memory.get_message_pieces_async(prompt_ids=list(scorable.message_piece_ids))
            conversation_id = scored[0].conversation_id if scored else None
        else:
            conversation_id = scorable.conversation_id if isinstance(scorable, ConversationScorable) else None
        if conversation_id is None:
            return None
        for piece in await self._memory.get_message_pieces_async(conversation_id=conversation_id):
            if piece.api_role != "assistant" or piece.is_simulated:
                continue
            if piece.is_blocked():
                return AssertSkipReason.TARGET_INPUT_REFUSED
            if piece.has_error() or piece.converted_value_data_type == "error":
                return AssertSkipReason.TARGET_ERROR
        return None
