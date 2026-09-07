# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from pyrit.models import ComponentIdentifier, MessageScorable, Scorable, Score, ScoringExpectation
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer


class ManualScorer(FloatScaleScorer):
    """Create a user-supplied score for one persisted message piece."""

    def __init__(self, *, value: float, rationale: str, success_threshold: float = 0.5) -> None:
        """
        Initialize the scorer.

        Args:
            value (float): User-supplied score in the range [0, 1].
            rationale (str): User-supplied explanation for the score.
            success_threshold (float): Minimum score considered successful.

        Raises:
            ValueError: If ``value`` or ``success_threshold`` is outside the range [0, 1].
        """
        if not 0 <= value <= 1:
            raise ValueError("Manual score value must be between 0 and 1.")
        if not 0 <= success_threshold <= 1:
            raise ValueError("Manual score success threshold must be between 0 and 1.")

        self._value = value
        self._rationale = rationale
        self._success_threshold = success_threshold
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
                "success_threshold": self._success_threshold,
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
                score_value=format(self._value, "g"),
                score_value_description="Manually assigned score",
                score_type="float_scale",
                score_rationale=self._rationale,
                score_metadata={"success_threshold": self._success_threshold},
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece_id,
                scorable=scorable,
                objective=expectation.objective if expectation else None,
            )
        ]
