# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.
# Source: assert_ai/core/judge.py (aggregate_judge_verdicts).
# PyRIT repeated judge samples: 2026-11-09.

"""Repeat an ASSERT dimension judge and aggregate its verdicts the way ASSERT does."""

from __future__ import annotations

import copy
import json
import logging
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.score.assert_dimension_scorer import AssertDimensionMetadata
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import _set_snapshot_participation
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import ComponentIdentifier, Scorable, ScorableUnion, Score, ScoringExpectation
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer import Scorer, SnapshotParticipation

logger = logging.getLogger(__name__)


class AssertSampleMetadata:
    """Score metadata keys written when an ASSERT judge is sampled more than once."""

    COUNT: ClassVar[str] = "assert_sample_count"
    VOTES: ClassVar[str] = "assert_sample_votes"
    AGREEMENT: ClassVar[str] = "assert_sample_agreement"
    MEAN: ClassVar[str] = "assert_sample_mean"
    ORIGINAL_VOTE: ClassVar[str] = "assert_voted_score_value"
    WARNINGS: ClassVar[str] = "assert_judgment_warnings"


class _AssertSampledScorer:
    """
    Shared sampling and aggregation for one ASSERT dimension judge.

    Boolean verdicts use a majority vote with ties counted as True (a failure), as in ASSERT.
    Ordinal grades use the lower median. Not-applicable samples are excluded from the vote; the
    result is not applicable only when no sample was applicable. Samples whose judgment failed are
    ignored, and the result is undetermined only when every sample failed. For a category-validated
    ``policy_violation`` judge, category judgments are aggregated by majority and the verdict is
    recomputed from them.
    """

    AGGREGATION_VERSION: ClassVar[int] = 1

    def __init__(self, *, scorer: Scorer, samples: int, dimension_name: str) -> None:
        if samples < 2:
            raise ValueError("A sampled ASSERT judge needs at least two samples; use the judge directly for one.")
        if samples % 2 == 0:
            logger.warning(
                "ASSERT judge '%s' uses %d samples. Ties count as failures; prefer an odd sample count.",
                dimension_name,
                samples,
            )
        self.scorer = scorer
        self.samples = samples
        self.dimension_name = dimension_name

    def identifier_params(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "dimension": self.dimension_name,
            "aggregation": f"assert_v{self.AGGREGATION_VERSION}",
        }

    async def score_async(
        self, *, wrapper: Scorer, scorable: Scorable, expectation: ScoringExpectation | None
    ) -> list[Score]:
        child_expectation = self.scorer._select_expectation(expectation=expectation)
        results = await gather_with_cleanup_async(
            [
                self.scorer._score_nested_async(scorable=scorable, expectation=child_expectation)
                for _ in range(self.samples)
            ]
        )
        if any(not scores for scores in results):
            return []
        samples = [scores[0] for scores in results]
        score = self._aggregate(wrapper=wrapper, samples=samples)
        score.scorable = cast("ScorableUnion", scorable)
        _set_snapshot_participation(score=score, child_scores={0: samples[0]})
        return [score]

    def _aggregate(self, *, wrapper: Scorer, samples: list[Score]) -> Score:
        complete = [score for score in samples if not score.is_undetermined]
        votes = [self._vote(score) for score in samples]
        if not complete:
            not_applicable = [score for score in samples if _is_not_applicable(score)]
            result = wrapper._create_wrapper_score(not_applicable[0] if not_applicable else samples[0])
            self._add_vote_metadata(score=result, votes=votes, agreement=None)
            return result

        aggregate = self._aggregate_values([score.get_value() for score in complete])
        node_value, nodes = self._aggregate_categories(complete)
        final = node_value if node_value is not None else aggregate
        representative = next((score for score in complete if score.get_value() == final), complete[0])
        result = wrapper._create_wrapper_score(representative)
        result.set_value(final)
        metadata = dict(result.score_metadata or {})
        metadata.pop(AssertDimensionMetadata.NOT_APPLICABLE, None)
        if nodes is not None:
            metadata["assert_node_judgments"] = json.dumps(nodes, ensure_ascii=True)
        if final != aggregate:
            metadata[AssertSampleMetadata.ORIGINAL_VOTE] = _format_vote(aggregate)
            warnings = json.loads(str(metadata.get(AssertSampleMetadata.WARNINGS, "[]")))
            warnings.append(f"aggregate_policy_violation_overridden_to_{'violated' if final else 'clear'}")
            metadata[AssertSampleMetadata.WARNINGS] = json.dumps(sorted(set(warnings)))
        result.score_metadata = metadata
        agreement = sum(1 for score in complete if score.get_value() == aggregate) / len(complete)
        self._add_vote_metadata(score=result, votes=votes, agreement=agreement)
        return result

    def _aggregate_values(self, values: list[bool | float]) -> bool | float:
        if isinstance(values[0], bool):
            failures = sum(1 for value in values if value)
            return failures >= len(values) - failures
        ordered = sorted(values)
        return ordered[(len(ordered) - 1) // 2]

    def _aggregate_categories(self, complete: list[Score]) -> tuple[bool | None, list[dict[str, object]] | None]:
        """
        Vote each category's relevance, then its violation, as ASSERT does.

        A sample that omits a category judged it not relevant. Relevance ties count as relevant,
        and violation ties among the relevant votes count as violated.

        Returns:
            tuple[bool | None, list[dict[str, object]] | None]: The recomputed policy violation and the
                voted relevant categories, or ``(None, None)`` when the judge has no category judgments.
        """
        if self.dimension_name != "policy_violation":
            return None, None
        judgments = [(score.score_metadata or {}).get("assert_node_judgments") for score in complete]
        if any(not isinstance(raw, str) for raw in judgments):
            return None, None
        by_name: dict[str, list[dict[str, object]]] = {}
        for raw in judgments:
            for node in json.loads(cast("str", raw)):
                by_name.setdefault(str(node["node_name"]), []).append(node)
        nodes = []
        for candidates in by_name.values():
            if len(candidates) < len(complete) - len(candidates):
                continue
            violated = sum(1 for node in candidates if node["violated"])
            verdict = violated >= len(candidates) - violated
            nodes.append(next(node for node in candidates if node["violated"] == verdict))
        return any(node["violated"] for node in nodes), nodes

    def _add_vote_metadata(self, *, score: Score, votes: list[str], agreement: float | None) -> None:
        metadata = dict(score.score_metadata or {})
        metadata[AssertSampleMetadata.COUNT] = self.samples
        metadata[AssertSampleMetadata.VOTES] = json.dumps(votes)
        if agreement is not None:
            metadata[AssertSampleMetadata.AGREEMENT] = round(agreement, 3)
        mean = self._numeric_grade_mean(score=score, votes=votes)
        if mean is not None:
            metadata[AssertSampleMetadata.MEAN] = mean
        score.score_metadata = metadata

    @staticmethod
    def _numeric_grade_mean(*, score: Score, votes: list[str]) -> float | None:
        if AssertDimensionMetadata.SCALE not in (score.score_metadata or {}):
            return None
        grades: list[object] = [json.loads(vote) for vote in votes if vote not in ("not_applicable", "undetermined")]
        numeric: list[int] = [grade for grade in grades if isinstance(grade, int) and not isinstance(grade, bool)]
        if not numeric or len(numeric) != len(grades):
            return None
        mean: float = sum(numeric) / len(numeric)
        return round(mean, 3)

    @staticmethod
    def _vote(score: Score) -> str:
        if _is_not_applicable(score):
            return "not_applicable"
        if score.is_undetermined:
            return "undetermined"
        grade = (score.score_metadata or {}).get(AssertDimensionMetadata.GRADE)
        if isinstance(grade, str):
            return grade
        return _format_vote(score.get_value())


class _AssertSampledTrueFalseScorer(TrueFalseScorer):
    """Sample a boolean ASSERT dimension judge and aggregate its verdicts."""

    def __init__(self, *, scorer: TrueFalseScorer, samples: int, dimension_name: str) -> None:
        """
        Wrap a boolean judge.

        Args:
            scorer (TrueFalseScorer): The judge to sample.
            samples (int): Independent judge calls per scoring; at least two.
            dimension_name (str): The dimension the judge scores.
        """
        self._sampler = _AssertSampledScorer(scorer=scorer, samples=samples, dimension_name=dimension_name)
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params=self._sampler.identifier_params(), sub_scorers=[self._sampler.scorer.get_identifier()]
        )

    def get_chat_target(self) -> PromptTarget | None:
        """
        Return the sampled judge's target.

        Returns:
            PromptTarget | None: The judge target.
        """
        return self._sampler.scorer.get_chat_target()

    def with_scorer_block_policy(self, *, raise_if_scorer_blocks: bool) -> Scorer:
        """
        Apply the blocked-judge policy to the sampled judge.

        Args:
            raise_if_scorer_blocks (bool): The policy to apply.

        Returns:
            Scorer: ``self`` when the judge is unchanged, otherwise a copy sampling the updated judge.
        """
        return _scope_block_policy(wrapper=self, raise_if_scorer_blocks=raise_if_scorer_blocks)

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        return (self._sampler.scorer,)

    async def _filter_matching_scores_by_snapshot_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        used_observation_ids: dict[str, set[str]],
        participation_by_score: dict[str, SnapshotParticipation | None],
    ) -> list[Score]:
        return await self._filter_matching_scores_by_children_async(
            scores=scores,
            scorable=scorable,
            expectation=expectation,
            used_observation_ids=used_observation_ids,
            participation_by_score=participation_by_score,
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return await self._sampler.score_async(wrapper=self, scorable=scorable, expectation=expectation)


class _AssertSampledFloatScaleScorer(FloatScaleScorer):
    """Sample an ordinal ASSERT dimension judge and aggregate its grades."""

    def __init__(self, *, scorer: FloatScaleScorer, samples: int, dimension_name: str) -> None:
        """
        Wrap an ordinal judge.

        Args:
            scorer (FloatScaleScorer): The judge to sample.
            samples (int): Independent judge calls per scoring; at least two.
            dimension_name (str): The dimension the judge scores.
        """
        self._sampler = _AssertSampledScorer(scorer=scorer, samples=samples, dimension_name=dimension_name)
        super().__init__()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params=self._sampler.identifier_params(), sub_scorers=[self._sampler.scorer.get_identifier()]
        )

    def get_chat_target(self) -> PromptTarget | None:
        """
        Return the sampled judge's target.

        Returns:
            PromptTarget | None: The judge target.
        """
        return self._sampler.scorer.get_chat_target()

    def with_scorer_block_policy(self, *, raise_if_scorer_blocks: bool) -> Scorer:
        """
        Apply the blocked-judge policy to the sampled judge.

        Args:
            raise_if_scorer_blocks (bool): The policy to apply.

        Returns:
            Scorer: ``self`` when the judge is unchanged, otherwise a copy sampling the updated judge.
        """
        return _scope_block_policy(wrapper=self, raise_if_scorer_blocks=raise_if_scorer_blocks)

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        return (self._sampler.scorer,)

    async def _filter_matching_scores_by_snapshot_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        used_observation_ids: dict[str, set[str]],
        participation_by_score: dict[str, SnapshotParticipation | None],
    ) -> list[Score]:
        return await self._filter_matching_scores_by_children_async(
            scores=scores,
            scorable=scorable,
            expectation=expectation,
            used_observation_ids=used_observation_ids,
            participation_by_score=participation_by_score,
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return await self._sampler.score_async(wrapper=self, scorable=scorable, expectation=expectation)


def _scope_block_policy(
    *, wrapper: _AssertSampledTrueFalseScorer | _AssertSampledFloatScaleScorer, raise_if_scorer_blocks: bool
) -> Scorer:
    sampler = wrapper._sampler
    scoped = sampler.scorer.with_scorer_block_policy(raise_if_scorer_blocks=raise_if_scorer_blocks)
    if scoped is sampler.scorer:
        return wrapper
    copied = copy.copy(wrapper)
    copied._sampler = copy.copy(sampler)
    copied._sampler.scorer = scoped
    return copied


def _is_not_applicable(score: Score) -> bool:
    return (score.score_metadata or {}).get(AssertDimensionMetadata.NOT_APPLICABLE) == "true"


def _format_vote(value: bool | float) -> str:
    return str(value).lower() if isinstance(value, bool) else json.dumps(value)
