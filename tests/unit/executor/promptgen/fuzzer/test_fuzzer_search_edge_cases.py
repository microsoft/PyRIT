# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import math
from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.promptgen.fuzzer import FuzzerContext, FuzzerGenerator, FuzzerShortenConverter
from pyrit.executor.promptgen.fuzzer.fuzzer import _MCTSExplorer, _PromptNode
from pyrit.score import TrueFalseScorer


def _explorer() -> _MCTSExplorer:
    return _MCTSExplorer(frequency_weight=0.5, reward_penalty=0.1, minimum_reward=0.2, non_leaf_node_probability=0.1)


def _node(*, visited_num: int, rewards: float) -> _PromptNode:
    node = _PromptNode("template {{ prompt }}")
    node.visited_num = visited_num
    node.rewards = rewards
    return node


@pytest.fixture
def generator() -> FuzzerGenerator:
    target = MockPromptTarget()
    return FuzzerGenerator(
        objective_target=target,
        template_converters=[FuzzerShortenConverter(converter_target=target)],
        scorer=MagicMock(TrueFalseScorer),
        target_jailbreak_goal_count=2,
    )


def test_uct_score_at_step_zero_matches_step_one_and_is_finite() -> None:
    explorer = _explorer()
    node = _node(visited_num=0, rewards=0.0)

    score_at_zero = explorer._calculate_uct_score(node=node, step=0)

    assert math.isfinite(score_at_zero)
    assert score_at_zero == explorer._calculate_uct_score(node=node, step=1)


def test_uct_score_prefers_less_visited_nodes_with_equal_rewards() -> None:
    explorer = _explorer()

    rarely_visited = explorer._calculate_uct_score(node=_node(visited_num=1, rewards=0.0), step=10)
    often_visited = explorer._calculate_uct_score(node=_node(visited_num=10, rewards=0.0), step=10)

    assert rarely_visited > often_visited


def test_uct_score_prefers_higher_rewards_with_equal_visits() -> None:
    explorer = _explorer()

    rewarding = explorer._calculate_uct_score(node=_node(visited_num=4, rewards=2.0), step=10)
    unrewarding = explorer._calculate_uct_score(node=_node(visited_num=4, rewards=0.0), step=10)

    assert rewarding > unrewarding


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("total_target_query_count", "total_jailbreak_count", "should_stop"),
    [
        (0, 0, False),
        (1, 0, False),
        (2, 0, True),
        (0, 1, False),
        (0, 2, True),
    ],
    ids=["fresh", "next-batch-fits-limit", "next-batch-exceeds-limit", "below-goal", "goal-reached"],
)
def test_should_stop_generation(
    generator: FuzzerGenerator, total_target_query_count: int, total_jailbreak_count: int, should_stop: bool
) -> None:
    context = FuzzerContext(prompts=["first", "second"], prompt_templates=["{{ prompt }}"], max_query_limit=3)
    context.total_target_query_count = total_target_query_count
    context.total_jailbreak_count = total_jailbreak_count

    assert generator._should_stop_generation(context) is should_stop


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("score_value", "expected"),
    [(True, 1.0), (False, 0.0), (0.25, 0.25), (1, 1.0), (1.5, 1.0), (-0.2, 0.0)],
    ids=["true", "false", "in-range", "int", "above-range", "below-range"],
)
def test_normalize_score_to_float(generator: FuzzerGenerator, score_value: object, expected: float) -> None:
    assert generator._normalize_score_to_float(score_value) == expected


@pytest.mark.usefixtures("patch_central_database")
def test_normalize_score_to_float_treats_unexpected_types_as_zero(
    generator: FuzzerGenerator, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert generator._normalize_score_to_float("high") == 0.0

    assert any("Unexpected score type" in record.message for record in caplog.records)
