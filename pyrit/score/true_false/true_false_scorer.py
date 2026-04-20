# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Optional

from pyrit.models import Message, Score
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics


class TrueFalseScorer(Scorer):
    """
    Base class for scorers that return true/false binary scores.

    This scorer evaluates prompt responses and returns a single boolean score indicating
    whether the response meets a specific criterion. Multiple pieces in a request response
    are aggregated using a TrueFalseAggregatorFunc function (default: TrueFalseScoreAggregator.OR).
    """

    # Default evaluation configuration - evaluates against all objective CSVs
    evaluation_file_mapping: Optional["ScorerEvalDatasetFiles"] = None

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the TrueFalseScorer.

        Args:
            validator (ScorerPromptValidator): Custom validator.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._score_aggregator = score_aggregator

        # Set default evaluation file mapping if not already set by subclass
        if self.evaluation_file_mapping is None:
            from pyrit.score.scorer_evaluation.scorer_evaluator import (
                ScorerEvalDatasetFiles,
            )

            self.evaluation_file_mapping = ScorerEvalDatasetFiles(
                human_labeled_datasets_files=["objective/*.csv"],
                result_file="objective/objective_achieved_metrics.jsonl",
            )

        super().__init__(validator=validator)

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate the scores returned by the scorer.

        Args:
            scores (list[Score]): The scores to be validated.

        Raises:
            ValueError: If the number of scores is not exactly one.
            ValueError: If the score value is not "true" or "false".
        """
        if len(scores) != 1:
            raise ValueError("TrueFalseScorer should return exactly one score.")

        if scores[0].score_value.lower() not in ["true", "false"]:
            raise ValueError("TrueFalseScorer score value must be True or False.")

    def get_scorer_metrics(self) -> Optional["ObjectiveScorerMetrics"]:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Returns:
            ObjectiveScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        from pyrit.common.path import SCORER_EVALS_PATH
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_hash,
        )

        if self.evaluation_file_mapping is None:
            return None

        result_file = SCORER_EVALS_PATH / self.evaluation_file_mapping.result_file

        if not result_file.exists():
            return None

        return find_objective_metrics_by_hash(hash=self.get_identifier().hash, file_path=result_file)

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        For TrueFalseScorer, multiple piece scores are aggregated into a single true/false score.

        Args:
            message (Message): The message to score.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list containing a single true/false Score object.

        Raises:
            ValueError: If no pieces are scored and cannot determine a piece ID for the return score.
        """
        # Get individual scores for all supported pieces using base implementation logic
        score_list = await super()._score_async(message, objective=objective)

        if not score_list:
            # If no pieces matched (e.g., due to role filter or if all pieces filtered), return False
            # Use the first message piece's ID (or original_prompt_id as fallback)
            first_piece = message.message_pieces[0]
            piece_id = first_piece.id or first_piece.original_prompt_id
            if piece_id is None:
                raise ValueError("Cannot create score: message piece has no id or original_prompt_id")

            # Determine specific rationale based on message piece status
            if first_piece.is_blocked():
                rationale = "The request was blocked by the target; returning false."
                description = "Blocked response; returning false."
            elif first_piece.has_error():
                rationale = f"Response had an error: {first_piece.response_error}; returning false."
                description = "Error response; returning false."
            else:
                # this can happen with multi-modal responses if no supported pieces are present
                rationale = "No supported pieces to score after filtering; returning false."
                description = "No pieces to score after filtering; returning false."

            return_score = Score(
                score_value=str(False).lower(),
                score_value_description=description,
                score_type="true_false",
                score_category=None,
                score_metadata=None,
                score_rationale=rationale,
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=piece_id,
                objective=objective,
            )
            return [return_score]

        # Use score aggregator to combine multiple piece scores into a single score
        result = self._score_aggregator(score_list)

        # Use the message_piece_id from the first score
        return_score = Score(
            score_value=str(result.value).lower(),
            score_value_description=result.description,
            score_type="true_false",
            score_category=result.category,
            score_metadata=result.metadata,
            score_rationale=result.rationale,
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=score_list[0].message_piece_id,
            objective=objective,
        )

        return [return_score]
