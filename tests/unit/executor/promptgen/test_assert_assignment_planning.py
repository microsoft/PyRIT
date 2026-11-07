# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
from collections import Counter

import pytest
from pydantic import ValidationError

from pyrit.executor.promptgen.assert_generator import _plan_assignments
from pyrit.models import AssertCoverage, AssertReplication, AssertSamplingConfig, AssertSamplingMethod


@pytest.mark.parametrize("method", list(AssertSamplingMethod))
def test_sampling_is_deterministic_and_preserves_count(method: AssertSamplingMethod) -> None:
    levels = {"behavior": ["allowed", "forbidden"], "channel": ["chat", "forum"], "persona": ["new", "returning"]}
    config = AssertSamplingConfig(method=method)
    state = random.getstate()
    first = _plan_assignments(levels=levels, count=17, sampling=config, random_seed=42)
    second = _plan_assignments(levels=levels, count=17, sampling=config, random_seed=42)
    assert first == second
    assert random.getstate() == state
    assert len(first) == 17
    assert all(set(row) == set(levels) for row in first)
    if method in {AssertSamplingMethod.PAIRWISE, AssertSamplingMethod.FULL_FACTORIAL}:
        assert AssertCoverage.from_assignments(levels=levels, assignments=first).pairwise_complete


def test_pairwise_coverage_is_measured_not_assumed() -> None:
    levels = {"behavior": ["a", "b", "c"], "channel": ["x", "y"], "persona": ["i", "j", "k"]}
    complete = _plan_assignments(levels=levels, count=100, sampling=AssertSamplingConfig(), random_seed=0)
    assert AssertCoverage.from_assignments(levels=levels, assignments=complete).pairwise_complete
    partial = _plan_assignments(levels=levels, count=1, sampling=AssertSamplingConfig(), random_seed=0)
    measured = AssertCoverage.from_assignments(levels=levels, assignments=partial)
    assert not measured.pairwise_complete
    assert measured.covered_pairs == 3
    assert measured.total_pairs == 21


@pytest.mark.parametrize("seed", range(5))
def test_pairwise_covers_more_than_three_axes(seed: int) -> None:
    levels = {f"axis_{index}": [str(level) for level in range(index + 1)] for index in range(5)}
    rows = _plan_assignments(levels=levels, count=100, sampling=AssertSamplingConfig(), random_seed=seed)
    assert AssertCoverage.from_assignments(levels=levels, assignments=rows).pairwise_complete


@pytest.mark.parametrize("count", [3, 17])
def test_stratified_sampling_balances_joint_strata(count: int) -> None:
    levels = {"behavior": ["a", "b"], "persona": ["new", "returning"], "channel": ["chat", "forum"]}
    sampling = AssertSamplingConfig(method=AssertSamplingMethod.STRATIFIED, stratify_by=("behavior", "persona"))
    rows = _plan_assignments(levels=levels, count=count, sampling=sampling, random_seed=1)
    counts = Counter((row["behavior"], row["persona"]) for row in rows)
    assert len(counts) == min(count, 4)
    assert max(counts.values()) - min(counts.values()) <= 1


def test_full_factorial_replication_is_balanced() -> None:
    levels = {"behavior": ["a", "b"], "channel": ["chat", "forum"]}
    sampling = AssertSamplingConfig(method=AssertSamplingMethod.FULL_FACTORIAL)
    rows = _plan_assignments(levels=levels, count=10, sampling=sampling, random_seed=0)
    counts = Counter(tuple(row.values()) for row in rows)
    assert len(counts) == 4
    assert max(counts.values()) - min(counts.values()) == 1
    no_replication = AssertSamplingConfig(
        method=AssertSamplingMethod.FULL_FACTORIAL, replication=AssertReplication.NONE
    )
    exact = _plan_assignments(levels=levels, count=4, sampling=no_replication, random_seed=0)
    assert len({tuple(row.values()) for row in exact}) == 4
    with pytest.raises(ValueError, match="count =="):
        _plan_assignments(levels=levels, count=5, sampling=no_replication, random_seed=0)
    with pytest.raises(ValueError, match="count >="):
        _plan_assignments(levels=levels, count=3, sampling=sampling, random_seed=0)


def test_random_sampling_without_replacement_handles_large_factorial_space() -> None:
    levels = {f"axis_{index}": ["a", "b"] for index in range(80)}
    sampling = AssertSamplingConfig(method=AssertSamplingMethod.RANDOM, with_replacement=False)
    rows = _plan_assignments(levels=levels, count=5, sampling=sampling, random_seed=0)
    assert len({tuple(row.values()) for row in rows}) == 5
    with pytest.raises(ValueError, match="factorial size"):
        _plan_assignments(levels={"behavior": ["a"]}, count=2, sampling=sampling, random_seed=0)


def test_single_axis_coverage_and_budget() -> None:
    levels = {"behavior": ["a", "b", "c"]}
    rows = _plan_assignments(levels=levels, count=8, sampling=AssertSamplingConfig(), random_seed=0)
    assert len(rows) == 8
    assert AssertCoverage.from_assignments(levels=levels, assignments=rows).pairwise_complete
    assert not AssertCoverage.from_assignments(levels=levels, assignments=rows[:1]).pairwise_complete


@pytest.mark.parametrize("assignments", [[{"behavior": "b"}], [{"unknown": "a"}], [{}]])
def test_invalid_assignment_does_not_produce_a_coverage_report(assignments: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError, match="unknown"):
        AssertCoverage.from_assignments(levels={"behavior": ["a"]}, assignments=assignments)


@pytest.mark.parametrize(
    "options",
    [
        {"stratify_by": []},
        {"stratify_by": ["behavior", "behavior"]},
        {"stratify_by": ["channel"]},
        {"replication": "none"},
        {"with_replacement": False},
    ],
)
def test_sampling_rejects_irrelevant_or_invalid_options(options: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AssertSamplingConfig.model_validate(options)


@pytest.mark.parametrize(
    "method, expected",
    [
        (
            AssertSamplingMethod.PAIRWISE,
            [
                ("allowed", "forum", "new"),
                ("allowed", "forum", "new"),
                ("forbidden", "forum", "returning"),
                ("forbidden", "forum", "returning"),
                ("forbidden", "chat", "new"),
                ("forbidden", "chat", "new"),
                ("allowed", "chat", "returning"),
                ("allowed", "chat", "returning"),
            ],
        ),
        (
            AssertSamplingMethod.STRATIFIED,
            [
                ("allowed", "forum", "new"),
                ("forbidden", "chat", "returning"),
                ("allowed", "chat", "new"),
                ("allowed", "forum", "new"),
                ("forbidden", "chat", "new"),
                ("forbidden", "chat", "new"),
                ("allowed", "forum", "new"),
                ("forbidden", "forum", "new"),
            ],
        ),
        (
            AssertSamplingMethod.FULL_FACTORIAL,
            [
                ("forbidden", "forum", "new"),
                ("forbidden", "forum", "returning"),
                ("allowed", "forum", "new"),
                ("forbidden", "chat", "new"),
                ("allowed", "chat", "new"),
                ("allowed", "forum", "returning"),
                ("allowed", "chat", "returning"),
                ("forbidden", "chat", "returning"),
            ],
        ),
        (
            AssertSamplingMethod.RANDOM,
            [
                ("forbidden", "chat", "returning"),
                ("allowed", "chat", "new"),
                ("forbidden", "chat", "new"),
                ("allowed", "chat", "returning"),
                ("forbidden", "chat", "new"),
                ("allowed", "forum", "new"),
                ("allowed", "chat", "new"),
                ("forbidden", "chat", "new"),
            ],
        ),
    ],
)
def test_assignments_match_pinned_assert_fixtures(
    method: AssertSamplingMethod, expected: list[tuple[str, str, str]]
) -> None:
    # ASSERT v0.3.0 (2026-09-04), revision 04b713fe73879208369750b3bd5ad53544f3eecc.
    levels = {"behavior": ["allowed", "forbidden"], "channel": ["chat", "forum"], "persona": ["new", "returning"]}
    rows = _plan_assignments(levels=levels, count=8, sampling=AssertSamplingConfig(method=method), random_seed=7)
    assert [tuple(row[axis] for axis in levels) for row in rows] == expected
