# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

from pyrit.models import (
    ComponentIdentifier,
    Condition,
    Scorable,
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
            ValueError: If the scorer is not a true/false scorer.
        """
        if not isinstance(scorer, TrueFalseScorer):
            raise ValueError("The scorer must be a true false scorer")
        self._scorer = scorer

        super().__init__()

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

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score the scorable with the wrapped scorer and invert the result.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: ``[]`` when the wrapped scorer is non-applicable; otherwise, a list
                containing its completed inverted score or unchanged undetermined score.
        """
        scores = await self._scorer._score_nested_async(scorable=scorable, expectation=expectation)
        if not scores:
            return []
        return self._invert(scores)

    def _invert(self, scores: list[Score]) -> list[Score]:
        """
        Flip a determined verdict, and leave an undetermined one alone.

        Polarity sits above the acquisition policy: there is nothing to invert when the
        wrapped scorer could not reach a verdict.

        Returns:
            list[Score]: A list containing the single inverted score.
        """
        inv_score = scores[0]
        scorer_type = self._scorer.get_identifier().class_name

        if inv_score.is_undetermined:
            inv_score.score_rationale = (
                f"Inverted score from {scorer_type} is undetermined\n{inv_score.score_rationale}"
            )
        else:
            inv_score.score_value = str(True) if not inv_score.get_value() else str(False)
            inv_score.score_value_description = "Inverted score: " + str(inv_score.score_value_description)
            inv_score.score_rationale = (
                f"Inverted score from {scorer_type} result: {inv_score.score_value}\n{inv_score.score_rationale}"
            )

        inv_score.id = uuid.uuid4()

        inv_score.scorer_class_identifier = self.get_identifier()

        return [inv_score]
