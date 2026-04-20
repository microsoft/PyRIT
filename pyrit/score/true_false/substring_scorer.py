# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.analytics.text_matching import ExactTextMatching, TextMatching
from pyrit.models import MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class SubStringScorer(TrueFalseScorer):
    """
    Scorer that checks if a given substring is present in the text.

    This scorer performs substring matching using a configurable text matching strategy.
    Supports both exact substring matching and approximate matching.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        substring: str,
        text_matcher: Optional[TextMatching] = None,
        categories: Optional[list[str]] = None,
        aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: Optional[ScorerPromptValidator] = None,
    ) -> None:
        """
        Initialize the SubStringScorer.

        Args:
            substring (str): The substring to search for in the text.
            text_matcher (Optional[TextMatching]): The text matching strategy to use.
                Defaults to ExactTextMatching with case_sensitive=False.
            categories (Optional[list[str]]): Optional list of categories for the score. Defaults to None.
            aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
        """
        self._substring = substring
        self._text_matcher = text_matcher if text_matcher else ExactTextMatching(case_sensitive=False)
        self._score_categories = categories if categories else []

        super().__init__(score_aggregator=aggregator, validator=validator or self._default_validator)

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            score_aggregator=self._score_aggregator.__name__,
            scorer_specific_params={
                "substring": self._substring,
                "text_matcher": self._text_matcher.__class__.__name__,
            },
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the given message piece based on presence of the substring.

        Args:
            message_piece (MessagePiece): The message piece to score.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.
                Currently not used for this scorer.

        Returns:
            list[Score]: A list containing a single Score object with a boolean value indicating
                whether the substring matches the text according to the matching strategy.
        """
        substring_present = self._text_matcher.is_match(target=self._substring, text=message_piece.converted_value)

        score = [
            Score(
                score_value=str(substring_present),
                score_value_description="",
                score_metadata=None,
                score_type="true_false",
                score_category=self._score_categories,
                score_rationale="",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]

        return score
