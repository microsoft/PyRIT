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

    **Default unreadable / blocked behavior**

    The return type is ``list[Score]``. Unsupported evidence returns ``[]``. An unreadable
    transport or protocol response for supported evidence returns a list containing an
    undetermined score. A fully blocked response returns a list containing a completed ``0.0``
    score. Subclasses can override ``_build_fallback_score`` when they need different domain
    semantics.
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

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Build the default result for a blocked, unreadable, or non-applicable response.

        Args:
            message (Message): The message whose first piece tells why nothing was scored.
            objective (str | None): The objective associated with this scoring call.

        Returns:
            list[Score]: ``[]`` for non-applicable evidence; a list containing a completed
                ``0.0`` score for a fully blocked response; or a list containing an
                undetermined score for another response error.
        """
        return self._build_neutral_fallback_score(message=message, objective=objective, neutral_value="0.0")
