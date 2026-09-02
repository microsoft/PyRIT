# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

from pyrit.models import (
    ComponentIdentifier,
    Condition,
    Scorable,
    ScorableUnion,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score.float_scale.float_scale_score_aggregator import FloatScaleAggregatorFunc, FloatScaleScoreAggregator
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class FloatScaleThresholdScorer(TrueFalseScorer):
    """A scorer that applies a threshold to a float scale score to make it a true/false score."""

    ORIGINAL_FLOAT_VALUE_KEY: str = ORIGINAL_FLOAT_VALUE_KEY

    def __init__(
        self,
        *,
        scorer: FloatScaleScorer,
        threshold: float,
        float_scale_aggregator: FloatScaleAggregatorFunc = FloatScaleScoreAggregator.MAX,
    ) -> None:
        """
        Initialize the FloatScaleThresholdScorer.

        Args:
            scorer (FloatScaleScorer): The underlying float scale scorer to use.
            threshold (float): The threshold value between 0 and 1. Scores >= threshold are True, otherwise False.
            float_scale_aggregator (FloatScaleAggregatorFunc): The aggregator function to use for combining
                multiple float scale scores. Defaults to FloatScaleScoreAggregator.MAX.

        Raises:
            ValueError: If the threshold is not between 0 and 1.
        """
        self._scorer = scorer
        self._threshold = threshold
        self._float_scale_aggregator = float_scale_aggregator

        super().__init__()

        if threshold <= 0 or threshold > 1:
            raise ValueError("The threshold must be between 0 and 1")

    @property
    def threshold(self) -> float:
        """The threshold value used for score comparison."""
        return self._threshold

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "threshold": self._threshold,
                "float_scale_aggregator": self._float_scale_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            sub_scorers=[self._scorer.get_identifier()],
        )

    def get_chat_target(self) -> "PromptTarget | None":
        """
        Delegate to the wrapped scorer.

        Returns:
            PromptTarget | None: The chat target from the wrapped scorer.
        """
        return self._scorer.get_chat_target()

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Report what the wrapped scorer matches.

        Returns:
            frozenset[type[Condition]]: The condition types the wrapped scorer routes.
        """
        return self._scorer.matched_conditions()

    def required_conditions(self) -> frozenset[type[Condition]]:
        """
        Report what the wrapped scorer requires.

        Returns:
            frozenset[type[Condition]]: The required condition types.
        """
        return self._scorer.required_conditions()

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score the scorable with the wrapped float-scale scorer and threshold the result.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: ``[]`` when the wrapped scorer is non-applicable; otherwise, a list
                containing one completed or undetermined true/false score.
        """
        scores = await self._scorer._score_nested_async(scorable=scorable, expectation=expectation)
        if not scores:
            return []
        return self._apply_threshold(
            scores=scores,
            expectation=expectation,
            # Score rejects a kind outside the union when it is constructed below.
            scorable=cast("ScorableUnion | None", scorable),
            message_piece_id=self._piece_id_from_scorable(scorable),
        )

    def _apply_threshold(
        self,
        *,
        scores: list[Score],
        expectation: ScoringExpectation | None,
        scorable: ScorableUnion | None,
        message_piece_id: uuid.UUID | str | None,
    ) -> list[Score]:
        """
        Turn the aggregated float value into a single true/false verdict.

        Returns:
            list[Score]: A list containing one completed or undetermined true/false score.
        """
        objective = expectation.objective if expectation else None

        # The wrapped scorer's non-applicable result returns before aggregation.
        aggregate_results = self._float_scale_aggregator(scores)
        aggregate_score = aggregate_results[0]
        aggregate_value = aggregate_score.value
        scorer_type = self._scorer.get_identifier().class_name

        if aggregate_value is None:
            # There is no value to compare against the threshold.
            return [
                Score(
                    score_type="true_false",
                    score_value=None,
                    status=ScoreStatus.UNDETERMINED,
                    score_value_description=aggregate_score.description,
                    score_rationale=(
                        f"based on {scorer_type}\nNo verdict was reachable, so the threshold "
                        f"{self._threshold} could not be applied.\n{aggregate_score.rationale}"
                    ),
                    score_category=aggregate_score.category,
                    score_metadata=dict(aggregate_score.metadata),
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=message_piece_id,
                    scorable=scorable,
                    objective=objective,
                )
            ]

        # Calculate threshold result
        threshold_result = aggregate_value >= self._threshold
        if aggregate_value > self._threshold:
            comparison_symbol = ">"
        elif aggregate_value < self._threshold:
            comparison_symbol = "<"
        else:
            comparison_symbol = "="

        score = scores[0]
        score.score_type = "true_false"
        score.score_value = str(threshold_result)
        score.status = ScoreStatus.COMPLETE
        # Carry the aggregate's category, metadata and rationale rather than the first
        # constituent score's. The threshold decision is made on the aggregate, so
        # describing it with scores[0] mislabels the result whenever the wrapped scorer
        # returns more than one score (e.g. AzureContentFilterScorer, one per harm
        # category): the value would say True while the category, rationale and metadata
        # described a different, possibly zero-valued, category.
        score.score_rationale = (
            f"based on {scorer_type}\n"
            f"Normalized scale score: {aggregate_value} {comparison_symbol} threshold {self._threshold}\n"
            f"Rationale for scale score: {aggregate_score.rationale}"
        )
        score.score_value_description = aggregate_score.description
        score.score_category = aggregate_score.category
        score.id = uuid.uuid4()
        score.scorer_class_identifier = self.get_identifier()
        # Store the original float value in metadata for granular comparison
        score.score_metadata = {
            **aggregate_score.metadata,
            ORIGINAL_FLOAT_VALUE_KEY: aggregate_value,
        }

        return [score]
