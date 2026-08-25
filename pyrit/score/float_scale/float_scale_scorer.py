# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import Message, Score, UndeterminedScoreError
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer import Scorer

if TYPE_CHECKING:
    from pyrit.prompt_target.common.prompt_target import PromptTarget
    from pyrit.score.message_scorable_resolver import MessageScorableResolver
    from pyrit.score.scorer_evaluation.scorer_metrics import HarmScorerMetrics
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class FloatScaleScorer(Scorer):
    """
    Family base for scorers that return a value in the range [0, 1].

    This is the family axis only — what a score means — and says nothing about what kind of
    evidence produced it. ``MessageFloatScaleScorer`` adds the message pipeline for scorers
    whose evidence is a message; wrapping scorers use this base directly so a threshold can
    sit over a child that scores something other than a message.
    """

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Build a single-element list containing a neutral ``0.0`` score when no pieces could be scored.

        Inspects the first message piece to produce a rationale/description that
        distinguishes blocked, error, and filtered cases.

        Args:
            message (Message): The message whose first piece is inspected for status.
            objective (str | None): The objective associated with this scoring call.

        Returns:
            list[Score]: A single-element list containing a ``0.0`` ``float_scale`` score
                attributed to the first piece.

        Raises:
            ValueError: If the first message piece has no ``id`` or ``original_prompt_id``.
        """
        first_piece = message.message_pieces[0]
        piece_id = first_piece.id or first_piece.original_prompt_id
        if piece_id is None:
            raise ValueError("Cannot create score: message piece has no id or original_prompt_id")

        if first_piece.is_blocked():
            rationale = "The response was blocked with no content to score; returning 0.0."
            description = "Blocked response; returning 0.0."
        elif first_piece.has_error():
            # A transport or protocol failure is not the target's answer, so there is no verdict.
            return [
                self._build_undetermined_score(
                    rationale=f"Response had an error: {first_piece.response_error}; no verdict was reachable.",
                    description="Error response; no verdict was reachable.",
                    message_piece_id=piece_id,
                    objective=objective,
                )
            ]
        else:
            rationale = "No supported pieces to score after filtering; returning 0.0."
            description = "No pieces to score after filtering; returning 0.0."

        return [
            Score(
                score_value="0.0",
                score_value_description=description,
                score_type="float_scale",
                score_category=None,
                score_metadata=None,
                score_rationale=rationale,
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=piece_id,
                objective=objective,
            )
        ]

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate that the returned scores are within the valid range [0, 1].

        Raises:
            ValueError: If any score is not between 0 and 1.
        """
        for score in scores:
            try:
                value = score.get_value()
            except UndeterminedScoreError:
                # An undetermined score carries no value, so there is nothing to range-check.
                continue
            if not (0 <= value <= 1):
                raise ValueError("FloatScaleScorer score value must be between 0 and 1.")

    def get_scorer_metrics(self) -> HarmScorerMetrics | None:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Returns:
            HarmScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import find_harm_metrics_by_eval_hash

        if self.evaluation_file_mapping is None or self.evaluation_file_mapping.harm_category is None:
            return None

        eval_hash = self.get_identifier().eval_hash
        if eval_hash is None:
            return None

        return find_harm_metrics_by_eval_hash(
            eval_hash=eval_hash,
            harm_category=self.evaluation_file_mapping.harm_category,
        )


class MessageFloatScaleScorer(FloatScaleScorer, MessageScorer):
    """
    Base class for scorers that return floating-point scores in the range [0, 1].

    This scorer evaluates prompt responses and returns numeric scores indicating the degree
    to which a response exhibits certain characteristics. Each piece in a request response
    is scored independently, returning one score per piece.

    **Default error / blocked behavior**

    When no supported pieces remain after validator filtering (e.g. the response is
    blocked, has another error type, or no piece matches the scorer's supported data
    types), the base ``score_async`` invokes ``_build_fallback_score`` and returns a
    single ``Score`` with value ``0.0``. The rationale distinguishes blocked / error /
    filtered cases. This mirrors ``TrueFalseScorer``'s ``False`` default so that
    downstream consumers (attack strategies, threshold wrappers) get a consistent,
    "attack did not succeed" value without each call site needing special-cased error
    handling. Subclasses that need different semantics (e.g. a refusal-style
    "blocked = True") should override ``_score_piece_async`` or ``_build_fallback_score``.
    """

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        chat_target: PromptTarget | None = None,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """
        Initialize the FloatScaleScorer.

        Args:
            validator: A validator object used to validate scores.
            chat_target: Optional chat target used by the scorer, forwarded to the base class
                for validation against ``TARGET_REQUIREMENTS``.
            message_resolver: Message evidence resolver.
        """
        super().__init__(
            validator=validator,
            chat_target=chat_target,
            message_resolver=message_resolver,
        )
