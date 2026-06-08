# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

from pyrit.models import ChatMessageRole, ComponentIdentifier, Message, MessagePiece, Score
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
            params={
                "score_aggregator": self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            },
            children={
                "sub_scorers": [self._scorer.get_identifier()],
            },
        )

    def get_chat_target(self) -> Optional["PromptTarget"]:
        """
        Delegate to the wrapped scorer.

        Returns:
            PromptTarget | None: The chat target from the wrapped scorer.
        """
        return self._scorer.get_chat_target()

    async def _score_async(
        self,
        message: Message,
        *,
        objective: str | None = None,
        role_filter: ChatMessageRole | None = None,
    ) -> list[Score]:
        """
        Scores the piece using the underlying true-false scorer and returns the inverted score.

        Args:
            message (Message): The message to score.
            objective (str | None): The objective to evaluate against (the original attacker model's objective).
                Defaults to None.
            role_filter (ChatMessageRole | None): Optional filter for message roles. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object with the inverted true/false value.
        """
        scores = await self._scorer.score_async(
            message,
            objective=objective,
            role_filter=role_filter,
        )

        # TrueFalseScorers only have a single score
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
