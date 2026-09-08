# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.models import Message, Score, ScoreStatus, UndeterminedScoreError
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.message_scorable_resolver import MessageScorableResolver
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
    from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class TrueFalseScorer(Scorer):
    """
    Family base for scorers that return a single true/false verdict.

    This is the family axis only — what a score means — and says nothing about what kind of
    evidence produced it. ``MessageTrueFalseScorer`` adds the message pipeline for scorers
    whose evidence is a message; wrapping scorers use this base directly so a composite can
    hold a child that scores something other than a message.
    """

    # Default evaluation configuration - evaluates against all objective CSVs
    evaluation_file_mapping: ScorerEvalDatasetFiles | None = None

    def __init__(
        self,
        *,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the true/false family state.

        Args:
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            **kwargs (Any): Forwarded to the remaining bases in the MRO.
        """
        self._score_aggregator = score_aggregator

        # Set default evaluation file mapping if not already set by subclass
        if self.evaluation_file_mapping is None:
            from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

            self.evaluation_file_mapping = ScorerEvalDatasetFiles(
                human_labeled_datasets_files=["objective/*.csv"],
                result_file="objective/objective_achieved_metrics.jsonl",
            )

        super().__init__(**kwargs)

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate the scores returned by the scorer.

        Args:
            scores (list[Score]): The scores to be validated.

        Raises:
            ValueError: If the number of scores is not exactly one.
            ValueError: If a complete score has no value, or a value that is not "true" or "false".
        """
        if len(scores) != 1:
            raise ValueError("TrueFalseScorer should return exactly one score.")

        score = scores[0]
        try:
            score.get_value()
        except UndeterminedScoreError:
            return

        if score.score_value is None:
            raise ValueError("A complete TrueFalseScorer score must carry a value. Mark it undetermined instead.")

        if str(score.score_value).lower() not in ["true", "false"]:
            raise ValueError("TrueFalseScorer score value must be True or False.")

    def get_scorer_metrics(self) -> ObjectiveScorerMetrics | None:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Returns:
            ObjectiveScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        from pyrit.common.path import SCORER_EVALS_PATH
        from pyrit.score.scorer_evaluation.scorer_metrics_io import find_objective_metrics_by_eval_hash

        if self.evaluation_file_mapping is None:
            return None

        result_file = SCORER_EVALS_PATH / self.evaluation_file_mapping.result_file

        if not result_file.exists():
            return None

        eval_hash = self.get_identifier().eval_hash
        if eval_hash is None:
            return None

        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash, file_path=result_file)


class MessageTrueFalseScorer(TrueFalseScorer, MessageScorer):
    """
    Base class for scorers that return true/false binary scores.

    This scorer evaluates prompt responses and returns a single boolean score indicating
    whether the response meets a specific criterion. Multiple pieces in a request response
    are aggregated using a TrueFalseAggregatorFunc function (default: TrueFalseScoreAggregator.OR).

    **Default unreadable / blocked behavior**

    The return type is ``list[Score]``. Unsupported evidence returns ``[]``. An unreadable
    transport or protocol response for supported evidence returns a list containing an
    undetermined score. A fully blocked response returns a list containing a completed
    ``False`` score. Subclasses can override ``_build_fallback_score`` when they need different
    domain semantics. For example, ``SelfAskRefusalScorer`` returns ``True`` on a blocked
    response.
    """

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        chat_target: PromptTarget | None = None,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """
        Initialize the TrueFalseScorer.

        Args:
            validator (ScorerPromptValidator): Custom validator.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            chat_target (PromptTarget | None): Optional chat target used by the scorer,
                forwarded to the base class for validation against ``TARGET_REQUIREMENTS``.
            message_resolver (MessageScorableResolver | None): Message evidence resolver.
        """
        super().__init__(
            score_aggregator=score_aggregator,
            validator=validator,
            chat_target=chat_target,
            message_resolver=message_resolver,
        )

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Build the default result for a blocked, unreadable, or non-applicable response.

        Args:
            message (Message): The message whose first piece tells why nothing was scored.
            objective (str | None): The objective associated with this scoring call.

        Returns:
            list[Score]: ``[]`` for non-applicable evidence; a list containing a completed
                ``False`` score for a fully blocked response; or a list containing an
                undetermined score for another response error.
        """
        return self._build_neutral_fallback_score(message=message, objective=objective, neutral_value="false")

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        For TrueFalseScorer, multiple piece scores are aggregated into a single true/false score.
        When no supported piece produces a score, this method returns an empty list. The
        message-scoring pipeline preserves that empty result for non-applicable evidence.
        It handles unreadable transport responses and fully blocked responses before this
        method runs.

        Args:
            message (Message): The message to score.
            objective (str | None): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: ``[]`` when no applicable piece produces a score; otherwise, a list
                containing one completed or undetermined aggregate score.
        """
        # Get individual scores for all supported pieces using base implementation logic
        score_list = await MessageScorer._score_async(self, message, objective=objective)

        if not score_list:
            return []

        # Use score aggregator to combine multiple piece scores into a single score
        result = self._score_aggregator(score_list)
        undetermined = result.value is None

        # Use the message_piece_id from the first score
        return [
            Score(
                score_value=None if undetermined else str(result.value).lower(),
                status=ScoreStatus.UNDETERMINED if undetermined else ScoreStatus.COMPLETE,
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
