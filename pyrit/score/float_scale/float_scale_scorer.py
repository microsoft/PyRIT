# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import TYPE_CHECKING, Dict, Optional
from uuid import UUID

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.models import PromptDataType, Score, UnvalidatedScore
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
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
    """

    def __init__(self, *, validator: ScorerPromptValidator) -> None:
        """
        Initialize the FloatScaleScorer.

        Args:
            validator: A validator object used to validate scores.
        """
        super().__init__(validator=validator)

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
            find_harm_metrics_by_hash,
        )

        if self.evaluation_file_mapping is None or self.evaluation_file_mapping.harm_category is None:
            return None
        scorer_hash = self.get_identifier().hash

        return find_harm_metrics_by_hash(hash=scorer_hash, harm_category=self.evaluation_file_mapping.harm_category)

    async def _score_value_with_llm(
        self,
        *,
        prompt_target: PromptChatTarget,
        system_prompt: str,
        message_value: str,
        message_data_type: PromptDataType,
        scored_prompt_id: str | UUID,
        category: Optional[str | UUID] = None,
        objective: Optional[str] = None,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
        attack_identifier: Optional[Dict[str, str]] = None,
    ) -> UnvalidatedScore:
        score: UnvalidatedScore | None = None
        try:
            score = await super()._score_value_with_llm(
                prompt_target=prompt_target,
                system_prompt=system_prompt,
                message_value=message_value,
                message_data_type=message_data_type,
                scored_prompt_id=scored_prompt_id,
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
            )
        return score
