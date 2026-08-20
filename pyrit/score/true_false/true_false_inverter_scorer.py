# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

from pyrit.models import (
    ComponentIdentifier,
    Condition,
    Message,
    MessagePiece,
    Score,
    ScoringExpectation,
)
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TrueFalseInverterScorer(TrueFalseScorer):
    """A scorer that inverts a true false score."""

    def __init__(self, *, scorer: TrueFalseScorer, validator: ScorerPromptValidator | None = None) -> None:
        """
        Initialize the TrueFalseInverterScorer.

        Args:
            scorer (TrueFalseScorer): The underlying true/false scorer whose results will be inverted.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
                Note: This parameter is present for signature compatibility but is not used.

        Raises:
            ValueError: If the scorer is not an instance of TrueFalseScorer.
        """
        if not isinstance(scorer, TrueFalseScorer):
            raise ValueError("The scorer must be a true false scorer")
        self._scorer = scorer

        super().__init__(validator=ScorerPromptValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            sub_scorers=[self._scorer.get_identifier()],
        )

    def get_chat_target(self) -> "PromptTarget | None":
        """
        Delegate to the wrapped scorer.

        Returns:
            PromptTarget | None: The chat target from the wrapped scorer.
        """
        return self._scorer.get_chat_target()

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Report what the wrapped scorer matches.

        Returns:
            frozenset[type[Condition]]: The condition types the wrapped scorer routes.
        """
        return self._scorer.matched_conditions()

    def required_conditions(self) -> frozenset[type[Condition]]:
        """
        Report what the wrapped scorer requires.

        Returns:
            frozenset[type[Condition]]: The required condition types.
        """
        return self._scorer.required_conditions()

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Scores the piece using the underlying true-false scorer and returns the inverted score.

        Args:
            message (Message): The message to score.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: A list containing a single Score object with the inverted true/false value.
        """
        scores = await self._scorer._score_nested_message_async(
            message=message,
            expectation=expectation,
        )
        inv_score = scores[0]

        inv_score.score_value = str(True) if not inv_score.get_value() else str(False)
        inv_score.score_value_description = "Inverted score: " + str(inv_score.score_value_description)

        scorer_type = self._scorer.get_identifier().class_name
        inv_score.score_rationale = (
            f"Inverted score from {scorer_type} result: {inv_score.score_value}\n{inv_score.score_rationale}"
        )

        inv_score.id = uuid.uuid4()

        inv_score.scorer_class_identifier = self.get_identifier()

        return [inv_score]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Indicate that True False Inverter scorers do not support piecewise scoring.

        Args:
            message_piece (MessagePiece): Unused.
            objective (str | None): Unused.

        Raises:
            NotImplementedError: Always, since composite scoring operates at the response level.
        """
        raise NotImplementedError("TrueFalseInverterScorer does not support piecewise scoring.")
