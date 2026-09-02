# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import uuid

    from pyrit.prompt_target import PromptTarget

from pyrit.models import (
    ComponentIdentifier,
    Condition,
    Scorable,
    ScorableUnion,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

logger = logging.getLogger(__name__)


class TrueFalseCompositeScorer(TrueFalseScorer):
    """
    Composite true/false scorer that aggregates results from other true/false scorers.

    This scorer invokes a collection of constituent true/false scorers and reduces their
    single-score outputs into one final true/false score using the supplied aggregation
    function (e.g., ``TrueFalseScoreAggregator.AND``, ``TrueFalseScoreAggregator.OR``,
    ``TrueFalseScoreAggregator.MAJORITY``).

    Children are true/false scorers of any evidence kind, so a scorer over a message can be
    composed with one over evidence that is not a message at all.
    """

    def __init__(
        self,
        *,
        aggregator: TrueFalseAggregatorFunc,
        scorers: list[TrueFalseScorer],
    ) -> None:
        """
        Initialize the composite scorer.

        Args:
            aggregator (TrueFalseAggregatorFunc): Aggregation function to combine child scores
                (e.g., ``TrueFalseScoreAggregator.AND``, ``TrueFalseScoreAggregator.OR``,
                ``TrueFalseScoreAggregator.MAJORITY``).
            scorers (list[TrueFalseScorerBase]): The constituent true/false scorers to invoke.

        Raises:
            ValueError: If no scorers are provided.
            ValueError: If any provided scorer is not a true/false scorer.
        """
        # Initialize base with the selected aggregator used by TrueFalseScorer logic
        super().__init__(score_aggregator=aggregator)

        if not scorers:
            raise ValueError("At least one scorer must be provided.")

        for scorer in scorers:
            if not isinstance(scorer, TrueFalseScorer):
                raise ValueError("All scorers must be true_false scorers.")

        self._scorers = scorers

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            sub_scorers=[s.get_identifier() for s in self._scorers],
        )

    def get_chat_target(self) -> "PromptTarget | None":
        """Return the chat target from the first sub-scorer that has one."""
        for scorer in self._scorers:
            target = scorer.get_chat_target()
            if target is not None:
                return target
        return None

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Report the union of what the constituent scorers match.

        Returns:
            frozenset[type[Condition]]: The condition types this composite routes.
        """
        conditions: set[type[Condition]] = set()
        for scorer in self._scorers:
            conditions.update(scorer.matched_conditions())
        return frozenset(conditions)

    def required_conditions(self) -> frozenset[type[Condition]]:
        """
        Report the union of conditions required by the constituent scorers.

        Returns:
            frozenset[type[Condition]]: The required condition types.
        """
        conditions: set[type[Condition]] = set()
        for scorer in self._scorers:
            conditions.update(scorer.required_conditions())
        return frozenset(conditions)

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score a scorable by forwarding it, unchanged, to every constituent scorer.

        Each child acquires the named evidence itself, so a child that needs a wider or
        different view of it is free to derive one.

        Args:
            scorable (Scorable): What to look at.
            expectation (ScoringExpectation | None): What the child scorers should look for.

        Returns:
            list[Score]: ``[]`` when every child is non-applicable; otherwise, a list
                containing one completed or undetermined aggregate score.
        """
        score_list_results = await asyncio.gather(
            *(scorer._score_nested_async(scorable=scorable, expectation=expectation) for scorer in self._scorers)
        )
        applicable_results = [scores for scores in score_list_results if scores]
        skipped_count = len(score_list_results) - len(applicable_results)
        if skipped_count:
            logger.debug("Ignoring %d non-applicable child scorer result(s) in composite scoring.", skipped_count)
        if not applicable_results:
            return []
        return [
            self._build_aggregate_score(
                score_list_results=applicable_results,
                expectation=expectation,
                # Score rejects a kind outside the union when it is constructed below.
                scorable=cast("ScorableUnion | None", scorable),
                message_piece_id=self._piece_id_from_scorable(scorable),
            )
        ]

    def _build_aggregate_score(
        self,
        *,
        score_list_results: list[list[Score]],
        expectation: ScoringExpectation | None,
        scorable: ScorableUnion | None,
        message_piece_id: "uuid.UUID | str | None",
    ) -> Score:
        """
        Reduce one score per child into the composite's single verdict.

        Args:
            score_list_results (list[list[Score]]): Each child's returned scores.
            expectation (ScoringExpectation | None): What the child scorers looked for.
            scorable (Scorable | None): What the composite was asked about.
            message_piece_id (uuid.UUID | str | None): The message piece anchor, when there is one.

        Returns:
            Score: The aggregated true/false score.

        Raises:
            ValueError: If any constituent scorer does not return exactly one score.
            ValueError: If no scores are generated from the request response pieces.
        """
        for score in score_list_results:
            if len(score) != 1:
                raise ValueError("Each TrueFalseScorer must return exactly one score.")

        score_list = [score[0] for score in score_list_results]

        if len(score_list) == 0:
            raise ValueError("No scores were generated from the request response pieces.")

        result = self._score_aggregator(score_list)
        undetermined = result.value is None

        return Score(
            score_value=None if undetermined else str(result.value),
            status=ScoreStatus.UNDETERMINED if undetermined else ScoreStatus.COMPLETE,
            score_value_description=result.description,
            score_type="true_false",
            score_category=result.category,
            score_metadata=result.metadata,
            score_rationale=result.rationale,
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=message_piece_id,
            scorable=scorable,
            objective=expectation.objective if expectation else None,
        )
