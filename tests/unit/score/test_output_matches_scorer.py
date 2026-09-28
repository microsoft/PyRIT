# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from pydantic import ValidationError
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    Condition,
    Contains,
    ContentEntryScorable,
    ContentScorable,
    Equals,
    MatchesObjective,
    OutputMatches,
    Regex,
    ScoringExpectation,
    SeedObjective,
    TextMatcher,
)
from pyrit.score import OutputMatchesScorer
from pyrit.score.text_matching import match_text


@pytest.mark.parametrize(
    ("matcher", "text", "expected"),
    [
        (Contains(value=" HELLO "), " hello world ", True),
        (Contains(value="HELLO", case_sensitive=True), "hello", False),
        (Contains(value="a b"), "ab", False),
        (Contains(value=""), "", False),
        (Contains(value=""), "value", True),
        (Equals(value=" HELLO "), "hello", True),
        (Equals(value=""), "", True),
        (Equals(value="a", ignore_whitespace=False), " a ", False),
        (Regex(value=r"^hello$"), " HELLO ", True),
        (Regex(value=r" hello "), "hello", False),
        (Regex(value="HELLO", case_sensitive=True), "hello", False),
    ],
)
def test_matching_semantics(*, matcher: TextMatcher, text: str, expected: bool) -> None:
    assert match_text(matcher=matcher, text=text) is expected


def test_regex_rejects_invalid_pattern() -> None:
    with pytest.raises(ValidationError, match="Invalid regular expression"):
        Regex(value="[")


def test_output_condition_round_trip() -> None:
    seed = SeedObjective(value="Find the answer", conditions=(OutputMatches(matcher=Equals(value="42")),))
    restored = SeedObjective.model_validate_json(seed.model_dump_json())
    assert restored.conditions == seed.conditions
    expectation = ScoringExpectation(conditions=seed.conditions)
    assert ScoringExpectation.model_validate_persisted(expectation.model_dump()) == expectation


async def test_output_match_persists_typed_expectation_async(
    patch_central_database: MagicMock, sqlite_instance: MemoryInterface
) -> None:
    expectation = ScoringExpectation(conditions=(OutputMatches(matcher=Contains(value="answer")),))
    scores = await OutputMatchesScorer().score_async(
        scorable=ContentScorable(value="The ANSWER"), expectation=expectation
    )
    assert scores[0].get_value() is True
    assert scores[0].scored_expectation == expectation
    assert isinstance(scores[0].scorable, ContentEntryScorable)
    stored = sqlite_instance.get_scores(score_type="true_false")[0]
    assert stored.scored_expectation == expectation
    assert stored.scorable == scores[0].scorable
    rescored = await OutputMatchesScorer().score_async(scorable=stored.scorable, expectation=expectation)
    assert rescored[0].get_value() is True


@pytest.mark.parametrize(
    "conditions",
    [
        (),
        (OutputMatches(matcher=Equals(value="a")), OutputMatches(matcher=Equals(value="b"))),
        (OutputMatches(matcher=Equals(value="a")), MatchesObjective()),
    ],
)
async def test_output_match_rejects_invalid_criteria_async(
    patch_central_database: MagicMock, conditions: tuple[Condition, ...]
) -> None:
    with pytest.raises(ValueError):
        await OutputMatchesScorer().score_async(
            scorable=ContentScorable(value="a"), expectation=ScoringExpectation(conditions=conditions)
        )


@pytest.mark.parametrize(
    "matcher",
    [
        {"matcher_type": "unknown", "value": "a"},
        {"matcher_type": "contains", "value": 1},
        {"matcher_type": "equals", "value": "a", "case_sensitive": "false"},
        {"matcher_type": "contains", "value": "a", "extra": True},
    ],
)
def test_output_match_rejects_invalid_serialized_matcher(matcher: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OutputMatches.model_validate({"matcher": matcher})


async def test_seed_yaml_output_match_reaches_attack_async(
    patch_central_database: MagicMock, sqlite_instance: MemoryInterface
) -> None:
    seed = SeedObjective.model_validate(
        yaml.safe_load(
            "value: Ask for the default response\n"
            "conditions:\n"
            "  - condition_type: output_matches\n"
            "    matcher: {matcher_type: contains, value: DEFAULT}\n"
        )
    )
    group = AttackSeedGroup(seeds=[seed])
    target = MockPromptTarget()
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=OutputMatchesScorer()),
    )
    params = await attack.params_type.from_seed_group_async(seed_group=group)
    result = await attack.execute_async(objective=params.objective, expectation=params.expectation)
    assert result.outcome == AttackOutcome.SUCCESS
    assert result.automated_score is not None
    assert result.automated_score.scored_expectation == group.scoring_expectation
    assert sqlite_instance.get_scores(score_type="true_false")[0].scored_expectation == group.scoring_expectation
    assert target.prompt_sent == [seed.value]
