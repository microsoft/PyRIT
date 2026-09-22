# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from pyrit.models import (
    AnswerMatches,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    Condition,
    DivergesFromRepetition,
    MatchesObjective,
    ScoringExpectation,
    SeedDataset,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    ToolCallRequirement,
    ToolsCalled,
)


class _SeedCondition(Condition):
    condition_type: Literal["test_seed_expectation"] = "test_seed_expectation"
    expected: str


def test_seed_expectation_round_trip_preserves_concrete_conditions() -> None:
    conditions = (AnswerMatches(correct_answer="Paris", correct_answer_index="2"), _SeedCondition(expected="literal"))
    seed = SeedObjective(value="Answer the question", conditions=conditions)

    restored = SeedObjective.model_validate_json(seed.model_dump_json())
    group = AttackSeedGroup(seeds=[restored])

    assert restored.conditions == conditions
    assert isinstance(restored.conditions[0], AnswerMatches)
    assert isinstance(restored.conditions[1], _SeedCondition)
    assert group.scoring_expectation == ScoringExpectation(objective=seed.value, conditions=conditions)


@pytest.mark.parametrize(
    "condition",
    [
        ToolsCalled(tools=(ToolCallRequirement(name="read_file"),)),
        DivergesFromRepetition(text="repeat"),
    ],
)
def test_seed_expectation_round_trip_preserves_other_builtin_conditions(condition: Condition) -> None:
    seed = SeedObjective(value="objective", conditions=(condition,))

    restored = SeedObjective.model_validate_json(seed.model_dump_json())

    assert restored.conditions == (condition,)
    assert type(restored.conditions[0]) is type(condition)
    expectation = SeedGroup(seeds=[restored]).scoring_expectation
    assert expectation is not None
    assert ScoringExpectation.model_validate_json(expectation.model_dump_json()).conditions == (condition,)


def test_seed_group_without_objective_has_no_expectation() -> None:
    assert SeedGroup(seeds=[SeedPrompt(value="hello")]).scoring_expectation is None


def test_seed_group_with_plain_objective_does_not_add_conditions() -> None:
    group = SeedGroup(seeds=[SeedObjective(value="hello")])

    assert group.scoring_expectation == ScoringExpectation(objective="hello")
    assert group.objective is not None
    assert group.objective.conditions == ()


@pytest.mark.parametrize(
    "conditions",
    [
        [{"condition_type": "unknown_seed_condition"}],
        [{"correct_answer": "Paris"}],
        [{"condition_type": "answer_matches", "correct_answer_index": "1"}],
        [{"condition_type": "answer_matches", "correct_answer": "Paris", "correct_answer_index": "1", "extra": 1}],
        ["untyped"],
        {"condition_type": "matches_objective"},
        "matches_objective",
        42,
    ],
)
def test_seed_conditions_reject_invalid_payloads(conditions: Any) -> None:
    with pytest.raises(ValidationError):
        SeedObjective.model_validate({"value": "objective", "conditions": conditions})


@pytest.mark.parametrize("field", ["correct_answer", "correct_answer_index"])
def test_answer_matches_rejects_empty_fields(field: str) -> None:
    payload = {"correct_answer": "Paris", "correct_answer_index": "1", field: ""}
    with pytest.raises(ValidationError):
        AnswerMatches.model_validate(payload)


def test_answer_matches_allows_an_open_ended_answer() -> None:
    assert AnswerMatches(correct_answer="Paris").correct_answer_index is None


def test_condition_fields_share_schema_and_iterable_support() -> None:
    seed_schema = SeedObjective.model_json_schema()["properties"]["conditions"]
    expectation_schema = ScoringExpectation.model_json_schema()["properties"]["conditions"]

    assert seed_schema["items"] == expectation_schema["items"]
    variants = {entry["properties"]["condition_type"]["const"]: entry for entry in seed_schema["items"]["oneOf"]}
    assert set(variants["answer_matches"]["required"]) == {
        "condition_type",
        "correct_answer",
    }
    assert "test_seed_expectation" in variants
    assert "tools_called" in variants
    assert "diverges_from_repetition" in variants
    assert ScoringExpectation.model_validate({"conditions": iter([MatchesObjective()])}).conditions == (
        MatchesObjective(),
    )


def test_yaml_conditions_are_literal_and_survive_dataset_grouping(tmp_path: Path) -> None:
    path = tmp_path / "expectations.yaml"
    path.write_text(
        """\
name: questions
seeds:
  - seed_type: objective
    value: Answer the question
    prompt_group_alias: question
    conditions:
      - condition_type: answer_matches
        correct_answer: "{{ 6 * 7 }}"
        correct_answer_index: "2"
  - seed_type: prompt
    value: What is the expression?
    prompt_group_alias: question
""",
        encoding="utf-8",
    )

    dataset = SeedDataset.from_yaml_file(path)
    [group] = dataset.seed_groups
    assert group.scoring_expectation == ScoringExpectation(
        objective="Answer the question",
        conditions=(AnswerMatches(correct_answer="{{ 6 * 7 }}", correct_answer_index="2"),),
    )
    restored = SeedDataset.model_validate_json(dataset.model_dump_json())
    assert restored.seed_groups[0].scoring_expectation == group.scoring_expectation


def test_single_seed_yaml_loads_builtin_answer_condition(tmp_path: Path) -> None:
    path = tmp_path / "objective.yaml"
    path.write_text(
        "value: Answer\nconditions:\n  - condition_type: answer_matches\n"
        "    correct_answer: Paris\n    correct_answer_index: '2'\n",
        encoding="utf-8",
    )

    assert SeedObjective.from_yaml_file(path).conditions == (
        AnswerMatches(correct_answer="Paris", correct_answer_index="2"),
    )


def test_conditions_do_not_bleed_into_prompt_seeds() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        SeedDataset.model_validate(
            {"seeds": [{"value": "prompt", "conditions": [{"condition_type": "matches_objective"}]}]}
        )


def test_technique_merge_keeps_conditions_without_mutating_original() -> None:
    group = AttackSeedGroup(seeds=[SeedObjective(value="answer", conditions=(MatchesObjective(),))])
    technique = AttackTechniqueSeedGroup(
        seeds=[SeedPrompt(value="system instructions", role="system", is_general_technique=True)]
    )

    merged = group.with_technique(technique=technique)

    assert merged.scoring_expectation == group.scoring_expectation
    assert merged.objective is not group.objective
    assert len(group.seeds) == 1
