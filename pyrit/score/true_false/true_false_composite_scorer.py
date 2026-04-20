# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import List, Optional

from pyrit.models import ChatMessageRole, Message, MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class TrueFalseCompositeScorer(TrueFalseScorer):
    """
    Composite true/false scorer that aggregates results from other true/false scorers.

    This scorer invokes a collection of constituent ``TrueFalseScorer`` instances and
    reduces their single-score outputs into one final true/false score using the supplied
    aggregation function (e.g., ``TrueFalseScoreAggregator.AND``, ``TrueFalseScoreAggregator.OR``,
    ``TrueFalseScoreAggregator.MAJORITY``).
    """

    def __init__(
        self,
        *,
        aggregator: TrueFalseAggregatorFunc,
        scorers: List[TrueFalseScorer],
    ) -> None:
        """
        Initialize the composite scorer.

        Args:
            aggregator (TrueFalseAggregatorFunc): Aggregation function to combine child scores
                (e.g., ``TrueFalseScoreAggregator.AND``, ``TrueFalseScoreAggregator.OR``,
                ``TrueFalseScoreAggregator.MAJORITY``).
            scorers (List[TrueFalseScorer]): The constituent true/false scorers to invoke.

        Raises:
            ValueError: If no scorers are provided.
            ValueError: If any provided scorer is not a TrueFalseScorer.
        """
        # Initialize base with the selected aggregator used by TrueFalseScorer logic
        # Validation is used by sub-scorers
        super().__init__(score_aggregator=aggregator, validator=ScorerPromptValidator())

        if not scorers:
            raise ValueError("At least one scorer must be provided.")

        for scorer in scorers:
            if not isinstance(scorer, TrueFalseScorer):
                raise ValueError("All scorers must be true_false scorers.")

        self._scorers = scorers

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            sub_scorers=self._scorers,
            score_aggregator=self._score_aggregator.__name__,
        )

    async def _score_async(
        self,
        message: Message,
        *,
        objective: Optional[str] = None,
        role_filter: Optional[ChatMessageRole] = None,
    ) -> list[Score]:
        """
        Score a request/response by combining results from all constituent scorers.

        Args:
            message (Message): The request/response to score.
            objective (Optional[str]): Scoring objective or context.
            role_filter (Optional[ChatMessageRole]): Optional filter for message roles. Defaults to None.

        Returns:
            list[Score]: A single-element list with the aggregated true/false score.

        Raises:
            ValueError: If any constituent scorer does not return exactly one score.
            ValueError: If no scores are generated from the request response pieces.
        """
        tasks = [
            scorer.score_async(message=message, objective=objective, role_filter=role_filter)
            for scorer in self._scorers
        ]

        # Run all response scorings concurrently
        score_list_results = await asyncio.gather(*tasks)

        for score in score_list_results:
            if len(score) != 1:
                raise ValueError("Each TrueFalseScorer must return exactly one score.")

        # Use score aggregator to return a single score
        score_list = [score[0] for score in score_list_results]

        if len(score_list) == 0:
            raise ValueError("No scores were generated from the request response pieces.")

        result = self._score_aggregator(score_list)

        # Ensure the message piece has an ID
        piece_id = message.message_pieces[0].id
        assert piece_id is not None, "Message piece must have an ID"

        return_score = Score(
            score_value=str(result.value),
            score_value_description=result.description,
            score_type="true_false",
            score_category=result.category,
            score_metadata=result.metadata,
            score_rationale=result.rationale,
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=piece_id,
            objective=objective,
        )

        return [return_score]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Composite scorers do not support piecewise scoring.

        Args:
            message_piece (MessagePiece): Unused.
            objective (Optional[str]): Unused.

        Raises:
            NotImplementedError: Always, since composite scoring operates at the response level.
        """
        raise NotImplementedError("TrueFalseCompositeScorer does not support piecewise scoring.")
