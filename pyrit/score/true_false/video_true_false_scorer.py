# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.models import MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.score.video_scorer import _BaseVideoScorer


class VideoTrueFalseScorer(TrueFalseScorer, _BaseVideoScorer):
    """
    A scorer that processes videos by extracting frames and scoring them using a true/false image scorer.

    The VideoTrueFalseScorer breaks down a video into frames and uses a true/false scoring mechanism.
    The frame scores are aggregated using a TrueFalseAggregatorFunc (default: TrueFalseScoreAggregator.OR,
    meaning if any frame meets the objective, the entire video is scored as True).
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["video_path"])

    def __init__(
        self,
        *,
        image_capable_scorer: TrueFalseScorer,
        num_sampled_frames: Optional[int] = None,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the VideoTrueFalseScorer.

        Args:
            image_capable_scorer: A TrueFalseScorer capable of processing images.
            num_sampled_frames: Number of frames to extract from the video for scoring (default: 5).
            validator: Validator for the scorer. Defaults to video_path data type validator.
            score_aggregator: Aggregator for combining frame scores. Defaults to TrueFalseScoreAggregator.OR.
        """
        _BaseVideoScorer.__init__(
            self, image_capable_scorer=image_capable_scorer, num_sampled_frames=num_sampled_frames
        )

        TrueFalseScorer.__init__(
            self, validator=validator or self._default_validator, score_aggregator=score_aggregator
        )

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            sub_scorers=[self.image_scorer],
            score_aggregator=self._score_aggregator.__name__,
            scorer_specific_params={
                "num_sampled_frames": self.num_sampled_frames,
            },
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score a single video piece by extracting frames and aggregating their scores.

        Args:
            message_piece: The message piece containing the video.
            objective: Optional objective description for scoring.

        Returns:
            List containing a single aggregated score for the video.
        """
        # Get scores for all frames
        frame_scores = await self._score_frames_async(message_piece=message_piece, objective=objective)

        # Use the TrueFalseAggregatorFunc to combine frame scores
        result = self._score_aggregator(frame_scores)

        # Get the ID from the message piece
        piece_id = message_piece.id if message_piece.id is not None else message_piece.original_prompt_id

        # Create the aggregated score using the aggregator result
        aggregate_score = Score(
            score_value=str(result.value).lower(),
            score_value_description=result.description,
            score_type="true_false",
            score_category=result.category,
            score_metadata=result.metadata,
            score_rationale=f"Video scored by analyzing {len(frame_scores)} frames.\n{result.rationale}",
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=piece_id,
            objective=objective,
        )

        return [aggregate_score]
