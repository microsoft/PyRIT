# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from pyrit.models import ComponentIdentifier, MessageScorable, Scorable, ScorableUnion, Score, ScoringExpectation
from pyrit.score.observation.execution import _merge_observation_ids
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY
from pyrit.score.scorer import (
    Scorer,
    SnapshotParticipation,
    _child_snapshot_participation,
    _participates_in_snapshot,
    _set_snapshot_participation,
    _validated_snapshot_participation,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


class _FallbackScorer(Scorer):
    """Shared execution for typed, single-result fallback scorers."""

    @abstractmethod
    def __init__(
        self,
        *,
        scorer: Scorer,
        fallback_scorer: Scorer,
        scorer_type: type[Scorer],
    ) -> None:
        """
        Validate that both children belong to one family and cover the same conditions.

        Raises:
            ValueError: If the children have different families or conditions, or are the same object.
        """
        if not isinstance(scorer, scorer_type) or not isinstance(fallback_scorer, scorer_type):
            raise ValueError(f"Both scorers must be instances of {scorer_type.__name__}.")
        if scorer is fallback_scorer:
            raise ValueError("The fallback scorer must be a different scorer from the primary.")
        if scorer.get_condition_types() != fallback_scorer.get_condition_types():
            raise ValueError("Fallback scorers must support the same condition types.")
        self._scorer = scorer
        self._fallback_scorer = fallback_scorer
        super().__init__()

    def get_chat_target(self) -> "PromptTarget | None":
        """Return the primary's chat target, or the fallback's if the primary has none."""
        target = self._scorer.get_chat_target()
        return target if target is not None else self._fallback_scorer.get_chat_target()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build an identifier that preserves primary and fallback order.

        Returns:
            ComponentIdentifier: The wrapper and its ordered children.
        """
        return self._create_identifier(
            sub_scorers=[self._scorer.get_identifier(), self._fallback_scorer.get_identifier()],
        )

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """Return both children for shared condition validation."""
        return (self._scorer, self._fallback_scorer)

    async def _filter_matching_scores_by_snapshot_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        used_observation_ids: dict[str, set[str]],
        participation_by_score: dict[str, SnapshotParticipation | None],
    ) -> list[Score]:
        """
        Validate snapshots only for the primary and fallback branches that produced a result.

        Returns:
            list[Score]: Candidates with fresh observations for every participating branch.
        """
        participation_by_score = {
            str(score.id): _validated_snapshot_participation(
                score=score,
                child_count=2,
                participation_by_score=participation_by_score,
            )
            for score in scores
        }
        primary_scores = [
            score
            for score in scores
            if _participates_in_snapshot(score=score, child_index=0, participation_by_score=participation_by_score)
        ]
        primary_participation = {
            str(score.id): _child_snapshot_participation(
                score=score, child_index=0, participation_by_score=participation_by_score
            )
            for score in primary_scores
        }
        primary_matches = await self._scorer._filter_matching_scores_by_snapshot_async(
            scores=primary_scores,
            scorable=scorable,
            expectation=self._scorer._select_expectation(expectation=expectation),
            used_observation_ids=used_observation_ids,
            participation_by_score=primary_participation,
        )
        primary_matches_ids = {str(score.id) for score in primary_matches}
        fallback_scores = [
            score
            for score in primary_matches
            if _participates_in_snapshot(score=score, child_index=1, participation_by_score=participation_by_score)
        ]
        fallback_ids: set[str] = set()
        if fallback_scores:
            fallback_expectation = self._fallback_scorer._select_expectation(expectation=expectation)
            valid_fallback_matches = await self._fallback_scorer._filter_matching_scores_by_snapshot_async(
                scores=fallback_scores,
                scorable=scorable,
                expectation=fallback_expectation,
                used_observation_ids=used_observation_ids,
                participation_by_score={
                    str(score.id): _child_snapshot_participation(
                        score=score, child_index=1, participation_by_score=participation_by_score
                    )
                    for score in fallback_scores
                },
            )
            fallback_ids = {str(score.id) for score in valid_fallback_matches}

        return [
            score
            for score in scores
            if str(score.id) in primary_matches_ids
            and (
                not _participates_in_snapshot(score=score, child_index=1, participation_by_score=participation_by_score)
                or str(score.id) in fallback_ids
            )
        ]

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        """
        Evaluate with the fallback only when the primary returns an undetermined score.

        Returns:
            list[Score]: One wrapper judgment, or no scores for a non-applicable primary.
        """
        primary = await self._score_child_async(scorer=self._scorer, scorable=scorable, expectation=expectation)
        if primary is None:
            return []
        if not primary.is_undetermined:
            result = self._build_result(primary=primary, scorable=scorable)
            _set_snapshot_participation(score=result, child_scores={0: primary})
            return [result]

        fallback = await self._score_child_async(
            scorer=self._fallback_scorer, scorable=scorable, expectation=expectation
        )
        if fallback is not None:
            self._validate_comparable_results(primary=primary, fallback=fallback)
        result = self._build_result(primary=primary, scorable=scorable, fallback=fallback, fallback_attempted=True)
        child_scores = {0: primary}
        if fallback is not None:
            child_scores[1] = fallback
        _set_snapshot_participation(score=result, child_scores=child_scores)
        return [result]

    async def _score_child_async(
        self, *, scorer: Scorer, scorable: Scorable, expectation: ScoringExpectation | None
    ) -> Score | None:
        """
        Evaluate one child without persisting its result.

        Returns:
            Score | None: The child's judgment, or None for unsupported evidence.

        Raises:
            ValueError: If an applicable child returns multiple scores or the wrong result family.
        """
        scores = await scorer._score_nested_async(
            scorable=scorable, expectation=scorer._select_expectation(expectation=expectation)
        )
        if len(scores) > 1:
            raise ValueError(
                f"{type(scorer).__name__} returned {len(scores)} scores. "
                "Fallback requires exactly one score from each applicable child; aggregate multiple results first."
            )
        if scores and scores[0].score_type != self.scorer_type:
            raise ValueError(f"Fallback requires a {self.scorer_type} result from each child.")
        return scores[0] if scores else None

    def _validate_comparable_results(self, *, primary: Score, fallback: Score) -> None:
        """
        Reject replacement judgments about different evidence or criteria.

        Raises:
            ValueError: If the results differ in evidence, declared categories, or expectation.
        """
        if primary.scorable != fallback.scorable or (
            (primary.scorable is None or isinstance(primary.scorable, MessageScorable))
            and str(primary.message_piece_id) != str(fallback.message_piece_id)
        ):
            raise ValueError("Fallback results must refer to the same evidence.")
        # Unreadable judgments can be undetermined without category labels.
        if (
            primary.score_category
            and fallback.score_category
            and set(primary.score_category) != set(fallback.score_category)
        ):
            raise ValueError("Fallback results must have the same categories.")
        if primary.scored_expectation != fallback.scored_expectation:
            raise ValueError("Fallback results must use the same scoring expectation.")

    def _build_result(
        self, *, primary: Score, scorable: Scorable, fallback: Score | None = None, fallback_attempted: bool = False
    ) -> Score:
        """
        Create a wrapper result without modifying either child score.

        Returns:
            Score: The selected judgment with separate metadata and combined observation links.
        """
        selected = fallback if fallback is not None else primary
        metadata: dict[str, str | int | float] = {
            f"primary.{key}": value for key, value in (primary.score_metadata or {}).items()
        }
        metadata["resolved_by"] = "fallback" if fallback is not None else "primary"
        # Attacks rank true/false scores by this top-level key, so keep the selected child's value.
        if selected.score_metadata and ORIGINAL_FLOAT_VALUE_KEY in selected.score_metadata:
            metadata[ORIGINAL_FLOAT_VALUE_KEY] = selected.score_metadata[ORIGINAL_FLOAT_VALUE_KEY]
        rationale = primary.score_rationale
        if fallback_attempted:
            metadata["primary_rationale"] = primary.score_rationale or ""
            metadata["fallback_status"] = fallback.status.value if fallback is not None else "not_applicable"
            if fallback is not None:
                metadata.update({f"fallback.{key}": value for key, value in (fallback.score_metadata or {}).items()})
                metadata["fallback_rationale"] = fallback.score_rationale or ""
            rationale = self._fallback_rationale(primary=primary, fallback=fallback)

        return Score(
            score_value=selected.score_value,
            status=selected.status,
            score_type=selected.score_type,
            score_value_description=selected.score_value_description,
            score_category=selected.score_category.copy() if selected.score_category is not None else None,
            score_rationale=rationale,
            score_metadata=metadata,
            scorer_class_identifier=self.get_identifier(),
            scorable=cast("ScorableUnion", scorable),
            message_piece_id=self._piece_id_from_scorable(scorable),
            scored_expectation=selected.scored_expectation,
            observation_ids=_merge_observation_ids(scores=[primary, fallback] if fallback is not None else [primary]),
        )

    def _fallback_rationale(self, *, primary: Score, fallback: Score | None) -> str:
        """
        Describe both attempts, including an unsupported fallback.

        Returns:
            str: Both evaluators' explanations and the fallback outcome.
        """
        lines = [f"Primary {type(self._scorer).__name__} returned an undetermined score."]
        if primary.score_rationale:
            lines.append(f"Primary rationale: {primary.score_rationale}")
        if fallback is None:
            lines.append(f"Fallback {type(self._fallback_scorer).__name__} was not applicable.")
        else:
            verdict = (
                "also returned an undetermined score"
                if fallback.is_undetermined
                else f"returned {fallback.score_value}"
            )
            lines.append(f"Fallback {type(self._fallback_scorer).__name__} {verdict}.")
            if fallback.score_rationale:
                lines.append(f"Fallback rationale: {fallback.score_rationale}")
        return "\n".join(lines)
