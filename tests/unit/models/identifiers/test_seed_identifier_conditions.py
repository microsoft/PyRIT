# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import AnswerMatches, MatchesObjective, SeedIdentifier, SeedObjective, SeedPrompt
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, config_hash
from pyrit.models.identifiers.seed_identifier import compute_seed_group_hash


@pytest.mark.parametrize("seed_class", [SeedObjective, SeedPrompt])
def test_condition_free_identifier_retains_legacy_hash(seed_class: type[SeedObjective] | type[SeedPrompt]) -> None:
    seed = seed_class(value="question", dataset_name="questions", data_type="text")
    legacy = ComponentIdentifier.of(
        seed,
        params={
            "value": seed.value,
            "value_sha256": seed.value_sha256,
            "data_type": seed.data_type,
            "dataset_name": seed.dataset_name,
            "is_general_technique": seed.is_general_technique,
        },
    )
    actual = SeedIdentifier.from_seed(seed)
    assert actual.hash == legacy.hash
    assert actual.model_dump() == legacy.model_dump()
    assert "conditions" not in actual.model_dump()
    assert compute_seed_group_hash([actual]) == config_hash(
        {"seed_identifiers": [legacy.model_dump(exclude={"hash", "eval_hash", "pyrit_version"})]}
    )
    assert SeedIdentifier.model_validate(legacy.model_dump()).hash == legacy.hash


def test_condition_identifier_and_group_hash_distinguish_answers() -> None:
    identifiers = [
        SeedIdentifier.from_seed(
            SeedObjective(
                value="question",
                conditions=(AnswerMatches(correct_answer=answer, correct_answer_index="A"),),
            )
        )
        for answer in ("Paris", "Rome")
    ]
    assert identifiers[0].hash != identifiers[1].hash
    assert compute_seed_group_hash([identifiers[0]]) != compute_seed_group_hash([identifiers[1]])
    for identifier in identifiers:
        assert SeedIdentifier.model_validate_json(identifier.model_dump_json()).model_dump() == identifier.model_dump()


def test_condition_identifier_canonicalization_preserves_condition_order() -> None:
    condition = AnswerMatches(correct_answer="Paris", correct_answer_index="A")
    first = SeedIdentifier.from_seed(SeedObjective(value="question", conditions=(condition, MatchesObjective())))
    equivalent = SeedIdentifier.from_seed(
        SeedObjective.model_validate(
            {
                "value": "question",
                "conditions": [
                    {"correct_answer_index": "A", "correct_answer": "Paris", "condition_type": "answer_matches"},
                    {"condition_type": "matches_objective"},
                ],
            }
        )
    )
    reversed_conditions = SeedIdentifier.from_seed(
        SeedObjective(value="question", conditions=(MatchesObjective(), condition))
    )
    assert first.hash == equivalent.hash
    assert compute_seed_group_hash([first]) == compute_seed_group_hash([equivalent])
    assert first.hash != reversed_conditions.hash
    assert compute_seed_group_hash([first]) != compute_seed_group_hash([reversed_conditions])


def test_legacy_seed_identifier_keys_load_without_conditions() -> None:
    identifier = SeedIdentifier.model_validate(
        {"__type__": "SeedObjective", "__module__": "pyrit.models.seeds.seed_objective", "value": "question"}
    )
    expected = ComponentIdentifier(
        class_name="SeedObjective", class_module="pyrit.models.seeds.seed_objective", params={"value": "question"}
    )
    assert identifier.hash == expected.hash
    assert identifier.model_dump() == expected.model_dump()
