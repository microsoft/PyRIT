# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from string import Formatter
from typing import TYPE_CHECKING

from pyrit.models import AnswerMatches, MessagePiece, Score, ScoringExpectation
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier


_ANSWER_PATTERN_FIELDS = frozenset({"correct_answer", "correct_answer_index"})


def _pattern_fields(pattern: str) -> set[str]:
    """
    Report the field names a matching pattern uses.

    Returns:
        set[str]: The names the pattern substitutes.
    """
    return {name for _, name, _, _ in Formatter().parse(pattern) if name}


def _format_answer_pattern(*, pattern: str, answer: AnswerMatches) -> str | None:
    """
    Format one matching pattern against the typed answer.

    Returns:
        str | None: The formatted pattern, or None when the pattern needs a field the
            condition does not carry.
    """
    fields: dict[str, str | None] = {
        "correct_answer": answer.correct_answer,
        "correct_answer_index": answer.correct_answer_index,
    }
    if any(fields[name] is None for name in _pattern_fields(pattern)):
        return None
    return pattern.format(**fields)


class QuestionAnswerScorer(MessageTrueFalseScorer):
    """
    A class that represents a question answering scorer.
    """

    CORRECT_ANSWER_MATCHING_PATTERNS = ["{correct_answer_index}:", "{correct_answer}"]
    MATCHED_CONDITIONS = frozenset({AnswerMatches})
    REQUIRED_CONDITIONS = frozenset({AnswerMatches})

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        correct_answer_matching_patterns: list[str] = CORRECT_ANSWER_MATCHING_PATTERNS,
        category: list[str] | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the QuestionAnswerScorer.

        Args:
            correct_answer_matching_patterns (list[str]): A list of patterns to check for in the response. If any
                pattern is found in the response, the score will be True. These patterns should be format strings
                that will be formatted with the AnswerMatches fields. Defaults to CORRECT_ANSWER_MATCHING_PATTERNS.
            category (list[str] | None): Optional list of categories for the score. Defaults to None.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If a pattern names a field that AnswerMatches does not carry.
            TypeError: If a subclass still overrides the objective-only piece hook.
        """
        if type(self)._score_piece_async is not MessageScorer._score_piece_async:
            raise TypeError(
                f"{type(self).__name__} overrides _score_piece_async, which QuestionAnswerScorer no longer calls. "
                "Move the custom policy to _score_piece_with_expectation_async."
            )
        unknown = {name for pattern in correct_answer_matching_patterns for name in _pattern_fields(pattern)} - (
            _ANSWER_PATTERN_FIELDS
        )
        if unknown:
            raise ValueError(f"The matching patterns name unknown field(s) {sorted(unknown)}.")
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
                "correct_answer_matching_patterns": self._correct_answer_matching_patterns,
                "answer_condition_version": 1,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
        )

    def _validate_expectation(self, *, expectation: ScoringExpectation | None) -> None:
        """
        Require typed ground truth, including on the objective-only compatibility path.

        Raises:
            ValueError: If the required answer condition is absent.
        """
        super()._validate_expectation(expectation=expectation)
        if expectation is None or not any(isinstance(item, AnswerMatches) for item in expectation.conditions):
            raise ValueError("QuestionAnswerScorer requires the condition AnswerMatches.")

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Match response text against the typed answer using the configured patterns.

        Returns:
            list[Score]: A single score indicating whether any pattern matched.
        """
        self._validate_expectation(expectation=expectation)
        assert expectation is not None
        answer = next(item for item in expectation.conditions if isinstance(item, AnswerMatches))
        result = False
        matching_text = None

        for pattern in self._correct_answer_matching_patterns:
            text = _format_answer_pattern(pattern=pattern, answer=answer)
            if text is None:
                continue
            if text.lower() in message_piece.converted_value.lower():
                result = True
                matching_text = text.lower()
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
                scored_expectation=expectation,
            )
        ]
