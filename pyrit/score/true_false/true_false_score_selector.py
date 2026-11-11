# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

from pyrit.score.true_false.multi_label_true_false_scorer import MultiLabelTrueFalseScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Scorable, Score, ScoringExpectation
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer import Scorer


class TrueFalseScoreSelector(TrueFalseScorer):
    """Project one named verdict while retaining the source's labels as intermediate scores."""

    def __init__(self, *, scorer: MultiLabelTrueFalseScorer, label: str) -> None:
        """
        Select the verdict used by a single-result consumer.

        Args:
            scorer (MultiLabelTrueFalseScorer): The scorer producing independent labeled verdicts.
            label (str): One of the scorer's declared labels.

        Raises:
            ValueError: If the source is not a multi-label scorer or the label is undeclared.
        """
        if not isinstance(scorer, MultiLabelTrueFalseScorer):
            raise ValueError("TrueFalseScoreSelector requires a MultiLabelTrueFalseScorer.")
        if label not in scorer.labels:
            raise ValueError(f"Unknown label {label!r}; choose one of {scorer.labels!r}.")
        self._scorer = scorer
        self._label = label
        super().__init__()

    def get_chat_target(self) -> PromptTarget | None:
        """Return the child's target for batch and evaluation rate-limit validation."""
        return self._scorer.get_chat_target()

    def with_scorer_block_policy(self, *, raise_if_scorer_blocks: bool) -> Scorer:
        """
        Apply blocked-judge policy to the child without mutating shared instances.

        Returns:
            Scorer: This selector or a copy containing the scoped child.
        """
        child = cast(
            "MultiLabelTrueFalseScorer",
            self._scorer.with_scorer_block_policy(raise_if_scorer_blocks=raise_if_scorer_blocks),
        )
        if child is self._scorer:
            return self
        scoped = copy.copy(self)
        scoped._scorer = child
        return scoped

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Include the selected label and complete source configuration in the identity.

        Returns:
            ComponentIdentifier: The selected label and source configuration.
        """
        return self._create_identifier(params={"label": self._label}, sub_scorers=[self._scorer.get_identifier()])

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """
        Expose the source for evidence and expectation preflight.

        Returns:
            tuple[Scorer, ...]: The multi-label child.
        """
        return (self._scorer,)

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        """
        Run the source once and copy its selected verdict into a distinct public result.

        Returns:
            list[Score]: The selected projection, or no result for non-applicable evidence.
        """
        scores = await self._scorer._score_nested_async(
            scorable=scorable, expectation=self._scorer._select_expectation(expectation=expectation)
        )
        if not scores:
            return []
        selected = next(score for score in scores if score.score_category == [self._label])
        return [self._create_wrapper_score(selected)]
