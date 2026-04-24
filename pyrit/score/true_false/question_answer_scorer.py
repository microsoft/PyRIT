# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pyrit.models import MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.identifiers import ComponentIdentifier


class QuestionAnswerScorer(TrueFalseScorer):
    """
    A class that represents a question answering scorer.
    """

    CORRECT_ANSWER_MATCHING_PATTERNS = ["{correct_answer_index}:", "{correct_answer}"]

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"], required_metadata=["correct_answer_index", "correct_answer"]
    )

    def __init__(
        self,
        *,
        correct_answer_matching_patterns: list[str] = CORRECT_ANSWER_MATCHING_PATTERNS,
        category: Optional[list[str]] = None,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the QuestionAnswerScorer.

        Args:
            correct_answer_matching_patterns (list[str]): A list of patterns to check for in the response. If any
                pattern is found in the response, the score will be True. These patterns should be format strings
                that will be formatted with the correct answer metadata. Defaults to CORRECT_ANSWER_MATCHING_PATTERNS.
            category (Optional[list[str]]): Optional list of categories for the score. Defaults to None.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._correct_answer_matching_patterns = correct_answer_matching_patterns
        self._score_category = category if category is not None else []

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, score_aggregator=score_aggregator)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "score_aggregator": self._score_aggregator.__name__,
                "correct_answer_matching_patterns": self._correct_answer_matching_patterns,
            },
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the message piece using question answering evaluation.

        Args:
            message_piece (MessagePiece): The answer given by the target, which must contain
                'correct_answer_index' and 'correct_answer' in prompt_metadata.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.
                Currently not used for this scorer.

        Returns:
            list[Score]: A list containing a single Score object indicating whether the correct answer was found.
        """
        result = False
        matching_text = None

        correct_index = message_piece.prompt_metadata["correct_answer_index"]
        correct_answer = message_piece.prompt_metadata["correct_answer"]

        for pattern in self._correct_answer_matching_patterns:
            text = pattern.format(correct_answer_index=correct_index, correct_answer=correct_answer).lower()
            if text in message_piece.converted_value.lower():
                result = True
                matching_text = text
                break

        return [
            Score(
                score_value=str(result),
                score_value_description="",
                score_metadata=None,
                score_type="true_false",
                score_category=self._score_category,
                score_rationale=(
                    f"Found matching text '{matching_text}' in response"
                    if matching_text
                    else "No matching text found in response"
                ),
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]
