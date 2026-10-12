# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Per-execution text matching through the shared condition contract."""

from pyrit.models import ComponentIdentifier, MessagePiece, OutputMatches, Score, ScoringExpectation
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.text_matching import match_text
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer


class OutputMatchesScorer(MessageTrueFalseScorer):
    """Evaluate converted text against one typed output criterion."""

    CONDITION_TYPE = OutputMatches
    _DEFAULT_VALIDATOR = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """Configure message validation and aggregation."""
        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, score_aggregator=score_aggregator)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"matching_version": 1},
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
        )

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        condition = self._get_required_condition(expectation=expectation, condition_type=OutputMatches)
        matched = match_text(matcher=condition.matcher, text=message_piece.converted_value)
        return [
            Score(
                score_value=str(matched).lower(),
                score_type="true_false",
                score_rationale=f"Output {'matches' if matched else 'does not match'} the text criterion.",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
            )
        ]
