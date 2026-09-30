# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from pyrit.models import ComponentIdentifier, Message, Scorable, Score, ScoreStatus, ScoreType, ScoringExpectation
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.observation.execution import _merge_observation_ids
from pyrit.score.scorer import Scorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.prompt_target import PromptTarget
    from pyrit.score.message_scorable_resolver import MessageScorableResolver
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class MultiLabelTrueFalseScorer(Scorer):
    """
    Return one independent true/false verdict for each declared label.

    Each score carries exactly one ``score_category``, its output label. Missing verdicts
    must be explicitly undetermined; ``[]`` means the evidence is not applicable.
    Use ``TrueFalseScoreSelector`` where a single objective verdict is required.
    """

    def __init__(self, *, labels: Sequence[str], **kwargs: Any) -> None:
        """
        Initialize the stable output labels and scorer dependencies.

        Args:
            labels (Sequence[str]): Nonempty, distinct labels in output order.
            **kwargs (Any): Arguments forwarded to the remaining scorer bases.

        Raises:
            ValueError: If labels are empty, blank, or repeated ignoring case.
        """
        if isinstance(labels, str) or not labels or any(not label or label != label.strip() for label in labels):
            raise ValueError("labels must be a nonempty sequence of nonblank, trimmed strings.")
        if len({label.casefold() for label in labels}) != len(labels):
            raise ValueError("labels must be unique, including case-insensitive comparisons.")
        self._labels = tuple(labels)
        super().__init__(**kwargs)

    @property
    def labels(self) -> tuple[str, ...]:
        """The declared output labels in stable order."""
        return self._labels

    @property
    def scorer_type(self) -> ScoreType:
        """The true/false value type of every labeled verdict."""
        return "true_false"

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate one true/false score per declared label.

        Raises:
            ValueError: If labels are missing, duplicated, or unknown, or a value is not true/false.
        """
        found: set[str] = set()
        for score in scores:
            categories = score.score_category or []
            if len(categories) != 1 or categories[0] not in self.labels:
                raise ValueError("Each labeled verdict must carry exactly one declared score_category.")
            label = categories[0]
            if label in found:
                raise ValueError(f"Duplicate verdict for label {label!r}.")
            found.add(label)
            if score.score_type != "true_false" or (
                not score.is_undetermined and str(score.score_value).lower() not in ("true", "false")
            ):
                raise ValueError(f"Verdict for {label!r} must be true/false or explicitly undetermined.")
        if found != set(self.labels):
            raise ValueError(f"Missing verdict labels: {sorted(set(self.labels) - found)}. Use undetermined scores.")

    def get_scorer_metrics(self) -> None:
        """Return no combined metric; evaluate a ``TrueFalseScoreSelector`` for each label."""

    def _create_identifier(self, *, params: dict[str, Any] | None = None, **kwargs: Any) -> ComponentIdentifier:
        """
        Include the declared output contract in every scorer identity.

        Returns:
            ComponentIdentifier: Identity including the labels and child-specific configuration.
        """
        return super()._create_identifier(params={**(params or {}), "labels": list(self.labels)}, **kwargs)


class MessageMultiLabelTrueFalseScorer(MultiLabelTrueFalseScorer, MessageScorer):
    """
    Score message pieces and aggregate each label independently.

    A piece scorer returns one anchored score per declared label, or ``[]`` for a
    non-applicable piece. No aggregator ever receives scores from different labels.
    Unreadable or fully blocked evidence leaves every label undetermined.
    """

    def __init__(
        self,
        *,
        labels: Sequence[str],
        validator: ScorerPromptValidator,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        chat_target: PromptTarget | None = None,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """Initialize the labels, per-label aggregator, and message acquisition policy."""
        self._score_aggregator = score_aggregator
        super().__init__(labels=labels, validator=validator, chat_target=chat_target, message_resolver=message_resolver)

    async def _score_async(
        self, message: Message, *, objective: str | None = None, expectation: ScoringExpectation | None = None
    ) -> list[Score]:
        """
        Aggregate the supported pieces within each label.

        Returns:
            list[Score]: One aggregate per label, or no scores if no piece applies.

        Raises:
            ValueError: If a piece returns invalid labels or an unrelated evidence anchor.
        """
        scores = await MessageScorer._score_async(self, message, objective=objective, expectation=expectation)
        if not scores:
            return []
        by_piece: dict[str, list[Score]] = defaultdict(list)
        piece_ids = {str(piece.id) for piece in message.message_pieces}
        for score in scores:
            if str(score.message_piece_id) not in piece_ids:
                raise ValueError("Each piece verdict must reference the message piece it scored.")
            by_piece[str(score.message_piece_id)].append(score)
        for piece_scores in by_piece.values():
            self.validate_return_scores(piece_scores)

        results: list[Score] = []
        for label in self.labels:
            labeled_scores = [score for score in scores if score.score_category == [label]]
            aggregate = self._score_aggregator(labeled_scores)
            results.append(
                Score(
                    score_value=None if aggregate.value is None else str(aggregate.value).lower(),
                    status=ScoreStatus.UNDETERMINED if aggregate.value is None else ScoreStatus.COMPLETE,
                    score_type="true_false",
                    score_category=[label],
                    score_rationale=aggregate.rationale,
                    score_value_description=aggregate.description,
                    score_metadata=aggregate.metadata,
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=labeled_scores[0].message_piece_id,
                    scorable=labeled_scores[0].scorable,
                    observation_ids=_merge_observation_ids(scores=labeled_scores),
                    objective=objective,
                )
            )
        return results

    def _label_undetermined_score(self, score: Score) -> list[Score]:
        """
        Preserve unavailable evidence as an undetermined verdict for each label.

        Returns:
            list[Score]: Separate score identities sharing the unavailable evidence.
        """
        return [
            score.model_copy(
                deep=True,
                update={
                    "id": uuid.uuid4(),
                    "score_category": [label],
                    "score_value": None,
                    "status": ScoreStatus.UNDETERMINED,
                    "score_value_description": (
                        score.score_value_description
                        if score.is_undetermined
                        else "No readable evidence for this label."
                    ),
                    "score_rationale": (
                        score.score_rationale
                        if score.is_undetermined
                        else "The response was blocked without readable content; every label remains undetermined."
                    ),
                },
            )
            for label in self.labels
        ]

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Preserve non-applicability and leave unreadable evidence undetermined.

        Returns:
            list[Score]: No scores for unsupported evidence, otherwise one unknown verdict per label.
        """
        fallback = self._build_neutral_fallback_score(message=message, objective=objective, neutral_value="false")
        return self._label_undetermined_score(fallback[0]) if fallback else []

    def _finalize_message_scores(
        self,
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
        expectation: ScoringExpectation | None,
    ) -> None:
        """Expand the shared blocked-judge fallback before anchoring all labeled verdicts."""
        if len(scores) == 1 and scores[0].is_undetermined and not scores[0].score_category:
            scores[:] = self._label_undetermined_score(scores[0])
        super()._finalize_message_scores(message=message, scores=scores, anchor=anchor, expectation=expectation)
