# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import TYPE_CHECKING

from pyrit.models import ComponentIdentifier, Scorable, Score, ScoringExpectation
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.multi_label_true_false_scorer import MultiLabelTrueFalseScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


class TrueFalseScoreSelector(TrueFalseScorer):
    """
    Project one named verdict into the single-score attack and evaluation contract.

    One child scoring operation supplies the selected verdict. As with other wrappers,
    only the projection is persisted; score the multi-label root directly to save all labels.
    Separate selector calls do not share or cache an inference.
    """

    def __init__(self, *, scorer: MultiLabelTrueFalseScorer, label: str) -> None:
        """
        Initialize a projection onto an explicitly declared label.

        Args:
            scorer (MultiLabelTrueFalseScorer): Scorer producing the labeled verdicts.
            label (str): Exact output label to select.

        Raises:
            ValueError: If the scorer is not multi-label or the label is not declared.
        """
        if not isinstance(scorer, MultiLabelTrueFalseScorer):
            raise ValueError("scorer must be a MultiLabelTrueFalseScorer.")
        if label not in scorer.labels:
            raise ValueError(f"Unknown label {label!r}. Expected one of {scorer.labels}.")
        self._scorer = scorer
        self._label = label
        self._prompt_target = scorer.get_chat_target()
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Include the selected label and source scorer in evaluation identity.

        Returns:
            ComponentIdentifier: Identity of this projection.
        """
        return self._create_identifier(params={"label": self._label}, sub_scorers=[self._scorer.get_identifier()])

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """Return the source for shared condition validation and routing."""
        return (self._scorer,)

    def get_chat_target(self) -> "PromptTarget | None":
        """Return the wrapped scorer's target for batching and rate-limit checks."""
        return self._scorer.get_chat_target()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        """
        Select by label after the source validates its complete output.

        Returns:
            list[Score]: The selected verdict with its evidence and status, or no applicable score.
        """
        scores = await self._scorer._score_nested_async(
            scorable=scorable, expectation=self._scorer._select_expectation(expectation=expectation)
        )
        return [
            score.model_copy(deep=True, update={"id": uuid.uuid4(), "scorer_class_identifier": self.get_identifier()})
            for score in scores
            if score.score_category == [self._label]
        ]
