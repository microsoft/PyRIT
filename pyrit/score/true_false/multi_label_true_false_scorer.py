# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pyrit.common.brick_contract import forward_init_parameters
from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.models import ComponentIdentifier, Message, Score, ScoreStatus, ScoringExpectation
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.observation.execution import _merge_observation_ids
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import MessagePiece, Scorable, ScoreType
    from pyrit.score.message_scorable_resolver import MessageScorableResolver
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class MultiLabelTrueFalseScorer(Scorer):
    """Result family with one independent true/false score per declared category."""

    @forward_init_parameters
    def __init__(self, *, labels: Sequence[str], **kwargs: Any) -> None:
        """
        Declare the labels produced by every applicable scoring operation.

        Args:
            labels (Sequence[str]): Nonempty category names, unique ignoring case.
            **kwargs (Any): Constructor arguments forwarded to the remaining bases.

        Raises:
            ValueError: If labels are empty, duplicated, or contain surrounding whitespace.
        """
        if isinstance(labels, (str, bytes)) or not labels:
            raise ValueError("labels must be a nonempty sequence of category names.")
        for label in labels:
            if not isinstance(label, str):  # type: ignore[ty:redundant-condition-strict]
                raise ValueError("Each label must be a string.")
            if not label.strip() or label != label.strip():
                raise ValueError("Each label must be nonempty without surrounding whitespace.")
        if len({label.lower() for label in labels}) != len(labels):
            raise ValueError("labels must be unique ignoring case.")
        self._labels = tuple(labels)
        super().__init__(**kwargs)

    @property
    def labels(self) -> tuple[str, ...]:
        """The ordered, immutable category names returned by this scorer."""
        return self._labels

    @property
    def scorer_type(self) -> ScoreType:
        """The true/false value type shared by the independent verdicts."""
        return "true_false"

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Require one distinct true/false result for each declared label.

        Raises:
            ValueError: If a nonempty result violates the declared output contract.
        """
        if not scores:
            return
        categories = [score.score_category for score in scores]
        if any(not category or len(category) != 1 for category in categories):
            raise ValueError("Each multi-label verdict must have exactly one score_category.")
        returned_labels = [category[0] for category in categories if category]
        if len(returned_labels) != len(self.labels) or set(returned_labels) != set(self.labels):
            raise ValueError("Multi-label scorers must return exactly one score for each declared label.")
        if len({score.id for score in scores}) != len(scores):
            raise ValueError("Each labeled verdict must have a distinct score ID.")
        for score in scores:
            if score.score_type != "true_false":
                raise ValueError("Every labeled verdict must have score_type='true_false'.")
            if not score.is_undetermined and str(score.score_value).lower() not in {"true", "false"}:
                raise ValueError("A completed labeled verdict must have a true/false value.")

    def get_scorer_metrics(self) -> ScorerMetrics | None:
        """Return no aggregate metrics; evaluate a label through ``TrueFalseScoreSelector``."""
        return None


class MessageMultiLabelTrueFalseScorer(MultiLabelTrueFalseScorer, MessageScorer):
    """
    Score supported message pieces once and aggregate independently within each label.

    Concrete scorers implement ``_score_piece_with_expectation_async`` and return one
    ``Score`` per label. Unsupported evidence returns no scores. Unreadable evidence,
    fully blocked responses, and suppressed judge blocks yield an undetermined verdict
    for every label. Concrete target-backed scorers own their ``TargetJudge``.
    """

    def __init__(
        self,
        *,
        labels: Sequence[str],
        validator: ScorerPromptValidator,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """
        Initialize the category-preserving message pipeline.

        Args:
            labels (Sequence[str]): Category names returned by each applicable piece.
            validator (ScorerPromptValidator): Message roles and data types to read.
            score_aggregator (TrueFalseAggregatorFunc): Aggregation within each label; defaults to OR.
            message_resolver (MessageScorableResolver | None): Optional evidence resolver.
        """
        self._score_aggregator = score_aggregator
        super().__init__(labels=labels, validator=validator, message_resolver=message_resolver)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Identify the output labels and their aggregation policy.

        Returns:
            ComponentIdentifier: The scorer's labels and aggregation configuration.
        """
        return self._create_identifier(
            params={"labels": list(self.labels)},
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
        )

    @abstractmethod
    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """Return one labeled score per declared category using the complete expectation."""
        ...

    async def _score_async(
        self, message: Message, *, objective: str | None = None, expectation: ScoringExpectation | None = None
    ) -> list[Score]:
        """
        Validate each piece's complete output before aggregating within labels.

        Returns:
            list[Score]: One aggregate per label, or no scores for non-applicable pieces.
        """
        if expectation is None and objective is not None:
            expectation = ScoringExpectation(objective=objective)
        piece_scores = await gather_with_cleanup_async(
            [
                self._score_piece_with_expectation_async(message_piece=piece, expectation=expectation)
                for piece in self._get_supported_pieces(message)
            ]
        )
        by_label: dict[str, list[Score]] = {label: [] for label in self.labels}
        for scores in piece_scores:
            self.validate_return_scores(scores)
            for score in scores:
                assert score.score_category is not None
                by_label[score.score_category[0]].append(score)

        results = []
        for label, scores in by_label.items():
            if not scores:
                continue
            result = self._score_aggregator(scores)
            results.append(
                Score(
                    score_value=None if result.value is None else str(result.value).lower(),
                    status=ScoreStatus.UNDETERMINED if result.value is None else ScoreStatus.COMPLETE,
                    score_type="true_false",
                    score_category=[label],
                    score_value_description=result.description,
                    score_rationale=result.rationale,
                    score_metadata=result.metadata,
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=scores[0].message_piece_id,
                    scorable=scores[0].scorable,
                    observation_ids=_merge_observation_ids(scores=scores),
                    scored_expectation=expectation,
                )
            )
        return results

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Preserve message applicability without inferring any label from a transport block.

        Returns:
            list[Score]: An undetermined fallback to expand, or no applicable scores.
        """
        scores = self._build_neutral_fallback_score(message=message, objective=objective, neutral_value="false")
        for score in scores:
            if not score.is_undetermined:
                score.status = ScoreStatus.UNDETERMINED
                score.score_value = None
                score.score_value_description = "Blocked evidence; no labeled verdict was reachable."
                score.score_rationale = "A transport block does not determine any of the declared labels."
        return scores

    async def _finalize_message_scores_async(
        self,
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
        expectation: ScoringExpectation | None,
    ) -> None:
        """Expand the shared undetermined fallback and retain its evidence and expectation."""
        if len(scores) == 1 and scores[0].is_undetermined and not scores[0].score_category:
            fallback = scores[0]
            scores[:] = [
                fallback.model_copy(deep=True, update={"id": uuid4(), "score_category": [label]})
                for label in self.labels
            ]
        await super()._finalize_message_scores_async(
            message=message, scores=scores, anchor=anchor, expectation=expectation
        )
