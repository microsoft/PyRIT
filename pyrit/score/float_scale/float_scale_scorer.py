# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Optional
from uuid import UUID

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, PromptDataType, Score, UnvalidatedScore
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.score.scorer_evaluation.scorer_metrics import HarmScorerMetrics


class FloatScaleScorer(Scorer):
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

    def __init__(self, *, validator: ScorerPromptValidator, chat_target: Optional[PromptTarget] = None) -> None:
        """
        Initialize the FloatScaleScorer.

        Args:
            validator: A validator object used to validate scores.
            chat_target: Optional chat target used by the scorer, forwarded to the base class
                for validation against ``TARGET_REQUIREMENTS``.
        """
        super().__init__(validator=validator, chat_target=chat_target)

    def _build_fallback_score(self, *, message: Message, objective: Optional[str]) -> list[Score]:
        """
        Build a single-element list containing a neutral ``0.0`` score when no pieces could be scored.

        Inspects the first message piece to produce a rationale/description that
        distinguishes blocked, error, and filtered cases.

        Args:
            message (Message): The message whose first piece is inspected for status.
            objective (Optional[str]): The objective associated with this scoring call.

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
            rationale = (
                "The request was blocked by the target "
                "(score_blocked_content is False or no partial content available); returning 0.0."
            )
            description = "Blocked response; returning 0.0."
        elif first_piece.has_error():
            rationale = f"Response had an error: {first_piece.response_error}; returning 0.0."
            description = "Error response; returning 0.0."
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
            if not (0 <= score.get_value() <= 1):
                raise ValueError("FloatScaleScorer score value must be between 0 and 1.")

    def get_scorer_metrics(self) -> Optional["HarmScorerMetrics"]:
        """
        Get evaluation metrics for this scorer from the configured evaluation result file.

        Returns:
            HarmScorerMetrics: The metrics for this scorer, or None if not found or not configured.
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_eval_hash,
        )

        if self.evaluation_file_mapping is None or self.evaluation_file_mapping.harm_category is None:
            return None

        eval_hash = self.get_identifier().eval_hash
        if eval_hash is None:
            return None

        return find_harm_metrics_by_eval_hash(
            eval_hash=eval_hash,
            harm_category=self.evaluation_file_mapping.harm_category,
        )

    async def _score_value_with_llm(
        self,
        *,
        prompt_target: PromptTarget,
        system_prompt: str,
        message_value: str,
        message_data_type: PromptDataType,
        scored_prompt_id: str | UUID,
        prepended_text_message_piece: Optional[str] = None,
        category: Optional[str | UUID] = None,
        objective: Optional[str] = None,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
        attack_identifier: Optional[ComponentIdentifier] = None,
    ) -> UnvalidatedScore:
        score: UnvalidatedScore | None = None
        try:
            score = await super()._score_value_with_llm(
                prompt_target=prompt_target,
                system_prompt=system_prompt,
                message_value=message_value,
                message_data_type=message_data_type,
                scored_prompt_id=scored_prompt_id,
                prepended_text_message_piece=prepended_text_message_piece,
                category=category,
                objective=objective,
                score_value_output_key=score_value_output_key,
                rationale_output_key=rationale_output_key,
                description_output_key=description_output_key,
                metadata_output_key=metadata_output_key,
                category_output_key=category_output_key,
                attack_identifier=attack_identifier,
            )
            if score is None:
                raise ValueError("Score returned None")
            # raise an exception if it's not parsable as a float
            float(score.raw_score_value)
        except ValueError:
            score_value = score.raw_score_value if score else "None"
            raise InvalidJsonException(
                message=(f"Invalid JSON response, score_value should be a float not this: {score_value}")
            ) from None
        return score
