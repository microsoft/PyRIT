# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Condition,
    MatchesObjective,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)


class _AlphaCondition(Condition):
    condition_type: Literal["test_expectation_alpha"] = "test_expectation_alpha"


class _BetaCondition(Condition):
    condition_type: Literal["test_expectation_beta"] = "test_expectation_beta"
    label: str = "b"


def test_expectation_defaults():
    expectation = ScoringExpectation()

    assert expectation.objective is None
    assert expectation.conditions == ()


def test_expectation_is_frozen():
    expectation = ScoringExpectation(objective="exfiltrate")

    with pytest.raises(ValidationError):
        expectation.objective = "something else"


def test_expectations_with_equal_values_compare_equal():
    assert ScoringExpectation(objective="a") == ScoringExpectation(objective="a")


def test_expectation_carries_conditions_beside_the_objective():
    expectation = ScoringExpectation(objective="exfiltrate", conditions=(MatchesObjective(),))

    assert expectation.objective == "exfiltrate"
    assert expectation.conditions == (MatchesObjective(),)


def test_expectation_carries_conditions_without_an_objective():
    expectation = ScoringExpectation(conditions=(MatchesObjective(),))

    assert expectation.objective is None
    assert expectation.conditions == (MatchesObjective(),)


def test_expectations_differing_only_in_conditions_compare_unequal():
    assert ScoringExpectation(objective="a") != ScoringExpectation(objective="a", conditions=(MatchesObjective(),))


def test_matches_objective_is_a_condition():
    assert isinstance(MatchesObjective(), Condition)


def test_matches_objective_instances_compare_equal():
    assert MatchesObjective() == MatchesObjective()


# --------------------------------------------------------------------------- #
# Versioned serialization (native Pydantic model_dump / model_validate)
# --------------------------------------------------------------------------- #
def test_objective_only_round_trip():
    expectation = ScoringExpectation(objective="do x")

    serialized = expectation.model_dump(mode="json")

    assert serialized == {"schema_version": 1, "objective": "do x", "conditions": []}
    assert ScoringExpectation.model_validate_persisted(serialized) == expectation


def test_condition_only_round_trip():
    expectation = ScoringExpectation(conditions=(MatchesObjective(),))

    serialized = expectation.model_dump(mode="json")

    assert serialized == {
        "schema_version": 1,
        "objective": None,
        "conditions": [{"condition_type": "matches_objective"}],
    }
    assert ScoringExpectation.model_validate_persisted(serialized) == expectation


def test_mixed_round_trip():
    expectation = ScoringExpectation(objective="do x", conditions=(MatchesObjective(),))

    assert ScoringExpectation.model_validate_persisted(expectation.model_dump(mode="json")) == expectation


def test_conditions_serialize_in_order():
    expectation = ScoringExpectation(conditions=(_AlphaCondition(), _BetaCondition(label="x")))

    serialized = expectation.model_dump(mode="json")

    assert [condition["condition_type"] for condition in serialized["conditions"]] == [
        "test_expectation_alpha",
        "test_expectation_beta",
    ]
    assert ScoringExpectation.model_validate_persisted(serialized) == expectation


def test_validate_rejects_unknown_schema_version():
    with pytest.raises(ValidationError, match="Unsupported ScoringExpectation schema_version"):
        ScoringExpectation.model_validate({"schema_version": 99, "objective": None, "conditions": []})


@pytest.mark.parametrize("schema_version", ["1", 1.0, True])
def test_validate_rejects_coerced_schema_version(schema_version):
    with pytest.raises(ValidationError, match="Unsupported ScoringExpectation schema_version"):
        ScoringExpectation.model_validate(
            {"schema_version": schema_version, "objective": None, "conditions": []}
        )


def test_persisted_validation_requires_schema_version():
    with pytest.raises(ValidationError, match="requires an explicit schema_version"):
        ScoringExpectation.model_validate_persisted({"objective": None, "conditions": []})


def test_validate_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        ScoringExpectation.model_validate({"schema_version": 1, "objective": None, "conditions": [], "extra": 1})


def test_validate_rejects_non_string_objective():
    with pytest.raises(ValidationError):
        ScoringExpectation.model_validate({"schema_version": 1, "objective": 5, "conditions": []})


def test_validate_rejects_unknown_condition_type():
    with pytest.raises(ValidationError, match="Unknown condition_type"):
        ScoringExpectation.model_validate(
            {"schema_version": 1, "objective": None, "conditions": [{"condition_type": "ghost"}]}
        )


def test_construction_rejects_non_string_objective():
    with pytest.raises(ValidationError):
        ScoringExpectation(objective=5)  # type: ignore[arg-type]


def test_construction_rejects_non_condition():
    with pytest.raises(ValidationError):
        ScoringExpectation(conditions=("not a condition",))  # type: ignore[arg-type]


def test_json_schema_describes_registered_condition_subtypes():
    condition_items = ScoringExpectation.model_json_schema()["properties"]["conditions"]["items"]

    assert condition_items["discriminator"] == {"propertyName": "condition_type"}
    condition_schemas = condition_items["oneOf"]
    condition_types = {
        schema["properties"]["condition_type"]["const"]: schema for schema in condition_schemas
    }
    assert "matches_objective" in condition_types
    assert "test_expectation_beta" in condition_types
    assert condition_types["test_expectation_beta"]["properties"]["label"]["type"] == "string"


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #
def test_fingerprint_ignores_construction_kwarg_order():
    first = ScoringExpectation(objective="o", conditions=(_BetaCondition(label="x"),))
    second = ScoringExpectation(conditions=(_BetaCondition(label="x"),), objective="o")

    assert scoring_expectation_fingerprint(first) == scoring_expectation_fingerprint(second)


def test_fingerprint_is_lowercase_sha256_hex():
    fingerprint = scoring_expectation_fingerprint(ScoringExpectation(objective="o"))

    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()


def test_fingerprint_depends_on_condition_order():
    forward = ScoringExpectation(conditions=(_AlphaCondition(), _BetaCondition(label="x")))
    reverse = ScoringExpectation(conditions=(_BetaCondition(label="x"), _AlphaCondition()))

    assert scoring_expectation_fingerprint(forward) != scoring_expectation_fingerprint(reverse)
