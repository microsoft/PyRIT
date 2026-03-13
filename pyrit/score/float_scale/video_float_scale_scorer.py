# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Optional

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import MessagePiece, Score
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleAggregatorFunc,
    FloatScaleScorerByCategory,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.video_scorer import _BaseVideoScorer

if TYPE_CHECKING:
    from pyrit.score.score_aggregator_result import ScoreAggregatorResult


class VideoFloatScaleScorer(
    FloatScaleScorer,
    _BaseVideoScorer,
):
    """
    A scorer that processes videos by extracting frames and scoring them using a float scale image scorer.

    The VideoFloatScaleScorer breaks down a video into frames and uses a float scale scoring mechanism.
    Frame scores are aggregated using a FloatScaleAggregatorFunc.

    By default, uses FloatScaleScorerByCategory.MAX which groups scores by category (useful for scorers like
    AzureContentFilterScorer that return multiple scores per frame). This returns one aggregated score
    per category (e.g., one for "Hate", one for "Violence", etc.).

    For scorers that return a single score per frame, or to combine all categories together,
    use FloatScaleScoreAggregator.MAX, FloatScaleScorerAllCategories.MAX, etc.

    Optionally, an audio_scorer can be provided to also score the video's audio track. When provided,
    the audio is extracted, transcribed, and scored. The audio scores are included in the aggregation.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["video_path"])

    def __init__(
        self,
        *,
        image_capable_scorer: FloatScaleScorer,
        audio_scorer: Optional[FloatScaleScorer] = None,
        num_sampled_frames: Optional[int] = None,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: FloatScaleAggregatorFunc = FloatScaleScorerByCategory.MAX,
        image_objective_template: Optional[str] = _BaseVideoScorer._DEFAULT_IMAGE_OBJECTIVE_TEMPLATE,
        audio_objective_template: Optional[str] = None,
    ) -> None:
        """
        Initialize the VideoFloatScaleScorer.

        Args:
            image_capable_scorer: A FloatScaleScorer capable of processing images.
            audio_scorer: Optional FloatScaleScorer for scoring the video's audio track.
                When provided, audio is extracted from the video, transcribed to text,
                and scored. The audio scores are aggregated with frame scores.
            num_sampled_frames: Number of frames to extract from the video for scoring (default: 5).
            validator: Validator for the scorer. Defaults to video_path data type validator.
            score_aggregator: Aggregator for combining frame scores. Defaults to FloatScaleScorerByCategory.MAX.
                Use FloatScaleScorerByCategory.MAX/AVERAGE/MIN for scorers that return multiple scores per frame
                (groups by category and returns one score per category).
                Use FloatScaleScorerAllCategories.MAX/AVERAGE/MIN to combine all scores regardless of category
                (returns single score with all categories combined).
                Use FloatScaleScoreAggregator.MAX/AVERAGE/MIN for simple aggregation preserving all categories
                (returns single score with all categories preserved).
            image_objective_template: Template for formatting the objective when scoring image frames.
                Use {objective} as placeholder for the actual objective. Set to None to not pass
                objective to image scorer. Defaults to a template that provides context about the
                video frame.
            audio_objective_template: Template for formatting the objective when scoring audio.
                Use {objective} as placeholder for the actual objective. Set to None to not pass
                objective to audio scorer. Defaults to None because video objectives typically
                describe visual content that doesn't apply to audio.

        Raises:
            ValueError: If audio_scorer is provided and does not support audio_path data type.
        """
        FloatScaleScorer.__init__(self, validator=validator or self._DEFAULT_VALIDATOR)

        _BaseVideoScorer.__init__(
            self,
            image_capable_scorer=image_capable_scorer,
            num_sampled_frames=num_sampled_frames,
            image_objective_template=image_objective_template,
            audio_objective_template=audio_objective_template,
        )
        self._score_aggregator = score_aggregator

        if audio_scorer is not None:
            self._validate_audio_scorer(audio_scorer)
        self.audio_scorer = audio_scorer

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the scorer evaluation identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        sub_scorer_ids = [self.image_scorer.get_identifier()]
        if self.audio_scorer:
            sub_scorer_ids.append(self.audio_scorer.get_identifier())

        return self._create_identifier(
            params={
                "score_aggregator": self._score_aggregator.__name__,
                "num_sampled_frames": self.num_sampled_frames,
                "has_audio_scorer": self.audio_scorer is not None,
                "image_objective_template": self.image_objective_template,
                "audio_objective_template": self.audio_objective_template,
            },
            children={
                "sub_scorers": sub_scorer_ids,
            },
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score a single video piece by extracting frames and optionally audio, then aggregating their scores.

        Args:
            message_piece: The message piece containing the video.
            objective: Optional objective description for scoring.

        Returns:
            List of aggregated scores for the video. Returns one score if using FloatScaleScoreAggregator,
            or multiple scores (one per category) if using FloatScaleScorerByCategory.
        """
        frame_scores = await self._score_frames_async(message_piece=message_piece, objective=objective)

        all_scores = list(frame_scores)
        audio_scored = False

        # Score audio if audio_scorer is provided
        if self.audio_scorer:
            audio_scores = await self._score_video_audio_async(
                message_piece=message_piece, audio_scorer=self.audio_scorer, objective=objective
            )
            if audio_scores:
                all_scores.extend(audio_scores)
                audio_scored = True

        # Get the ID from the message piece
        piece_id = message_piece.id if message_piece.id is not None else message_piece.original_prompt_id

        # Call the aggregator - all aggregators now return List[ScoreAggregatorResult]
        aggregator_results: list[ScoreAggregatorResult] = self._score_aggregator(all_scores)

        # Build rationale prefix
        rationale_prefix = f"Video scored by analyzing {len(frame_scores)} frames"
        if audio_scored:
            rationale_prefix += " and audio transcript"

        # Create Score objects from aggregator results
        aggregate_scores: list[Score] = []
        for result in aggregator_results:
            aggregate_score = Score(
                score_value=str(result.value),
                score_value_description=result.description,
                score_type="float_scale",
                score_category=result.category,
                score_metadata=result.metadata,
                score_rationale=f"{rationale_prefix}.\n{result.rationale}",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=piece_id,
                objective=objective,
            )
            aggregate_scores.append(aggregate_score)

        return aggregate_scores
