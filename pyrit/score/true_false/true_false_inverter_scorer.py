# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import ChatMessageRole, Message, MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TrueFalseInverterScorer(TrueFalseScorer):
    """A scorer that inverts a true false score."""

    def __init__(self, *, scorer: TrueFalseScorer, validator: Optional[ScorerPromptValidator] = None) -> None:
        """
        Initialize the TrueFalseInverterScorer.

        Args:
            scorer (TrueFalseScorer): The underlying true/false scorer whose results will be inverted.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
                Note: This parameter is present for signature compatibility but is not used.

        Raises:
            ValueError: If the scorer is not an instance of TrueFalseScorer.
        """
        if not isinstance(scorer, TrueFalseScorer):
            raise ValueError("The scorer must be a true false scorer")
        self._scorer = scorer

        # Reuse the inner scorer's validator so the inverter accepts (and rejects) the
        # same inputs as its wrapped scorer. Using a default validator would mask
        # validation mismatches and feed the inner scorer pieces it cannot handle.
        super().__init__(validator=scorer._validator)

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
            Optional[PromptTarget]: The chat target from the wrapped scorer.
        """
        return self._scorer.get_chat_target()

    async def _score_async(
        self,
        message: Message,
        *,
        objective: Optional[str] = None,
        role_filter: Optional[ChatMessageRole] = None,
    ) -> list[Score]:
        """
        Scores the piece using the underlying true-false scorer and returns the inverted score.

        Calls the inner scorer's ``_score_async`` directly (not its public ``score_async``)
        so the inner scorer's fallback does not fire before the inverter sees the result.
        If the inner scorer produces no scores (e.g. the message was blocked or had an
        unsupported data type), an empty list is returned so that the base ``score_async``
        invokes this inverter's own ``_build_fallback_score`` (``False`` = "attack did not
        succeed") instead of inverting the inner's fallback (which would yield ``True`` =
        "attack succeeded", a misleading semantic for generic wrapped scorers).

        Args:
            message (Message): The message to score.
            objective (Optional[str]): The objective to evaluate against (the original attacker model's objective).
                Defaults to None.
            role_filter (Optional[ChatMessageRole]): Optional filter for message roles. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object with the inverted true/false value,
                or an empty list when the inner scorer produced no scores.
        """
        scores = await self._scorer._score_async(
            message,
            objective=objective,
        )

        if not scores:
            return []

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

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Indicate that True False Inverter scorers do not support piecewise scoring.

        Args:
            message_piece (MessagePiece): Unused.
            objective (Optional[str]): Unused.

        Raises:
            NotImplementedError: Always, since composite scoring operates at the response level.
        """
        raise NotImplementedError("TrueFalseInverterScorer does not support piecewise scoring.")
