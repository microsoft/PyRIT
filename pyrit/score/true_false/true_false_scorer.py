# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import Message, Score
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class TrueFalseScorer(Scorer):
    """
    Base class for scorers that return true/false binary scores.

    This scorer evaluates prompt responses and returns a single boolean score indicating
    whether the response meets a specific criterion. Multiple pieces in a request response
    are aggregated using a TrueFalseAggregatorFunc function (default: TrueFalseScoreAggregator.OR).

    **Default error / blocked behavior**

    When no supported pieces remain after validator filtering (e.g. the response is
    blocked, has another error type, or no piece matches the scorer's supported data
    types), the base ``score_async`` invokes ``_build_fallback_score`` and returns a
    single ``Score(False)`` whose rationale distinguishes blocked / error / filtered
    cases. This mirrors ``FloatScaleScorer``'s ``0.0`` default so that downstream
    consumers (attack strategies, threshold wrappers) get a consistent, "attack did not
    succeed" value without each call site needing special-cased error handling.
    Subclasses that need different semantics (e.g. ``SelfAskRefusalScorer``, which
    returns ``True`` on blocked) should override ``_score_piece_async`` and accept the
    error data type in their validator.
    """

    # Default evaluation configuration - evaluates against all objective CSVs
    evaluation_file_mapping: ScorerEvalDatasetFiles | None = None

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        chat_target: PromptTarget | None = None,
    ) -> None:
        """
        Initialize the TrueFalseScorer.

        Args:
            validator (ScorerPromptValidator): Custom validator.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            chat_target (PromptTarget | None): Optional chat target used by the scorer,
                forwarded to the base class for validation against ``TARGET_REQUIREMENTS``.
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

        super().__init__(validator=validator, chat_target=chat_target)

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

    def get_scorer_metrics(self) -> ObjectiveScorerMetrics | None:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Returns:
            ObjectiveScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        from pyrit.common.path import SCORER_EVALS_PATH
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        if self.evaluation_file_mapping is None:
            return None

        result_file = SCORER_EVALS_PATH / self.evaluation_file_mapping.result_file

        if not result_file.exists():
            return None

        eval_hash = self.get_identifier().eval_hash
        if eval_hash is None:
            return None

        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash, file_path=result_file)

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        For TrueFalseScorer, multiple piece scores are aggregated into a single true/false score.
        When no supported pieces remain (e.g. the response was blocked, had an error, or no piece
        type matched the validator), returns an empty list; the base ``score_async`` then invokes
        ``_build_fallback_score`` to produce a single neutral ``Score(False)``.

        Args:
            message (Message): The message to score.
            objective (str | None): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list containing a single aggregated true/false Score, or an empty
                list when no pieces could be scored (the base class will supply a fallback).
        """
        # Get individual scores for all supported pieces using base implementation logic
        score_list = await super()._score_async(message, objective=objective)

        if not score_list:
            return []

        # Use score aggregator to combine multiple piece scores into a single score
        result = self._score_aggregator(score_list)

        # Use the message_piece_id from the first score
        return [
            Score(
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
        ]

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Build a single-element list containing a ``false`` score when no pieces could be scored.

        Inspects the first message piece to produce a rationale/description that
        distinguishes blocked, error, and filtered cases.

        Args:
            message (Message): The message whose first piece is inspected for status.
            objective (str | None): The objective associated with this scoring call.

        Returns:
            list[Score]: A single-element list containing a ``false`` ``true_false`` score
                attributed to the first piece.

        Raises:
            ValueError: If the first message piece has no ``id`` or ``original_prompt_id``.
        """
        first_piece = message.message_pieces[0]
        piece_id = first_piece.id or first_piece.original_prompt_id
        if piece_id is None:
            raise ValueError("Cannot create score: message piece has no id or original_prompt_id")

        if first_piece.is_blocked():
            rationale = (
                "The request was blocked by the target "
                "(score_blocked_content is False or no partial content available); returning false."
            )
            description = "Blocked response; returning false."
        elif first_piece.has_error():
            rationale = f"Response had an error: {first_piece.response_error}; returning false."
            description = "Error response; returning false."
        else:
            # this can happen with multi-modal responses if no supported pieces are present
            rationale = "No supported pieces to score after filtering; returning false."
            description = "No pieces to score after filtering; returning false."

        return [
            Score(
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
        ]
