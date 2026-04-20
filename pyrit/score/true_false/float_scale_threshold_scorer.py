# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import Optional

from pyrit.models import ChatMessageRole, Message, MessagePiece, Score
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleAggregatorFunc,
    FloatScaleScoreAggregator,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
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

        # Validation is used by sub-scorers
        super().__init__(validator=ScorerPromptValidator())

        if threshold <= 0 or threshold > 1:
            raise ValueError("The threshold must be between 0 and 1")

    @property
    def threshold(self) -> float:
        """Get the threshold value used for score comparison."""
        return self._threshold

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            sub_scorers=[self._scorer],
            score_aggregator=self._score_aggregator.__name__,
            scorer_specific_params={
                "threshold": self._threshold,
                "float_scale_aggregator": self._float_scale_aggregator.__name__,
            },
        )

    async def _score_async(
        self,
        message: Message,
        *,
        objective: Optional[str] = None,
        role_filter: Optional[ChatMessageRole] = None,
    ) -> list[Score]:
        """
        Scores the piece using the underlying float-scale scorer and thresholds the resulting score.

        Args:
            message (Message): The message to score.
            objective (Optional[str]): The objective to evaluate against (the original attacker model's objective).
                Defaults to None.
            role_filter (Optional[ChatMessageRole]): Optional filter for message roles. Defaults to None.

        Returns:
            list[Score]: A list containing a single true/false Score object based on the threshold comparison.
        """
        scores = await self._scorer.score_async(
            message,
            objective=objective,
            role_filter=role_filter,
        )

        # Aggregator handles 0-many scores and returns exactly one result (or raises if configured)
        aggregate_results = self._float_scale_aggregator(scores)
        aggregate_score = aggregate_results[0]
        aggregate_value = aggregate_score.value

        # Calculate threshold result
        threshold_result = aggregate_value >= self._threshold
        if aggregate_value > self._threshold:
            comparison_symbol = ">"
        elif aggregate_value < self._threshold:
            comparison_symbol = "<"
        else:
            comparison_symbol = "="

        scorer_type = self._scorer.get_identifier().class_name

        # If we have scores, modify the first one; otherwise create a new score
        if scores:
            score = scores[0]
            score.score_type = "true_false"
            score.score_value = str(threshold_result)
            score.score_rationale = (
                f"based on {scorer_type}\n"
                f"Normalized scale score: {aggregate_value} {comparison_symbol} threshold {self._threshold}\n"
                f"Rationale for scale score: {score.score_rationale}"
            )
            score.score_value_description = aggregate_score.description
            score.id = uuid.uuid4()
            score.scorer_class_identifier = self.get_identifier()
            # Store the original float value in metadata for granular comparison
            if score.score_metadata is None:
                score.score_metadata = {}
            score.score_metadata[ORIGINAL_FLOAT_VALUE_KEY] = aggregate_value
        else:
            # Create new score from aggregator result (all pieces were filtered out)
            # Use the first message piece's id if available, otherwise generate a new UUID
            piece_id = (
                message.message_pieces[0].id
                if message.message_pieces and message.message_pieces[0].id
                else uuid.uuid4()
            )

            score = Score(
                score_type="true_false",
                score_value=str(threshold_result),
                score_value_description=aggregate_score.description,
                score_rationale=(
                    f"based on {scorer_type}\n"
                    f"Normalized scale score: {aggregate_value} {comparison_symbol} threshold {self._threshold}\n"
                    f"{aggregate_score.rationale}"
                ),
                score_category=aggregate_score.category,
                # Include original float value in metadata for granular comparison
                score_metadata={
                    **aggregate_score.metadata,
                    ORIGINAL_FLOAT_VALUE_KEY: aggregate_value,
                },
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=piece_id,
                objective=objective,
            )

        return [score]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Float Scale scorers do not support piecewise scoring.

        Args:
            message_piece (MessagePiece): Unused.
            objective (Optional[str]): Unused.

        Raises:
            NotImplementedError: Always, since composite scoring operates at the response level.
        """
        raise NotImplementedError("FloatScaleThresholdScorer does not support piecewise scoring.")
