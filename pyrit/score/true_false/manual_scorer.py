# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from pyrit.models import ComponentIdentifier, MessageScorable, Scorable, Score, ScoringExpectation
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class ManualScorer(TrueFalseScorer):
    """Create a user-supplied score for one persisted message piece."""

    def __init__(self, *, value: bool, rationale: str, user_identifier: str) -> None:
        """
        Initialize the scorer.

        Args:
            value (bool): User-supplied objective verdict.
            rationale (str): User-supplied explanation for the score.
            user_identifier (str): Identifier for the user who supplied the score.
        """
        self._value = value
        self._rationale = rationale
        self._user_identifier = user_identifier
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the scorer identifier.

        Returns:
            ComponentIdentifier: The scorer identifier.
        """
        return self._create_identifier(
            params={
                "value": self._value,
                "rationale": self._rationale,
            }
        )

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Create the configured score for a persisted message piece.

        Returns:
            list[Score]: A list containing the manual score.

        Raises:
            TypeError: If ``scorable`` does not identify a message piece.
            ValueError: If ``scorable`` identifies more than one message piece.
        """
        if not isinstance(scorable, MessageScorable):
            raise TypeError("ManualScorer requires a MessageScorable.")
        if len(scorable.message_piece_ids) != 1:
            raise ValueError("ManualScorer requires exactly one message piece.")

        message_piece_id = scorable.message_piece_ids[0]
        return [
            Score(
                score_value=str(self._value),
                score_value_description="Manually assigned objective verdict",
                score_type="true_false",
                score_rationale=self._rationale,
                score_metadata={"user_identifier": self._user_identifier},
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece_id,
                scorable=scorable,
                objective=expectation.objective if expectation else None,
            )
        ]
