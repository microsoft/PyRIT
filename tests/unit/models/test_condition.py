# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Condition,
    DivergesFromRepetition,
    MatchesObjective,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)
from pyrit.models.score.condition import _CONDITION_TYPES


class _KeywordCondition(Condition):
    condition_type: Literal["test_keyword_condition"] = "test_keyword_condition"
    keyword: str


def test_matches_objective_has_stable_discriminator():
    assert MatchesObjective().condition_type == "matches_objective"
    assert _CONDITION_TYPES["matches_objective"] is MatchesObjective


def test_explicit_discriminator_is_registered():
    assert _KeywordCondition(keyword="k").condition_type == "test_keyword_condition"
    assert _CONDITION_TYPES["test_keyword_condition"] is _KeywordCondition


def test_condition_subclass_requires_discriminator_field():
    with pytest.raises(TypeError, match="single non-empty string Literal"):

        class _MissingDiscriminatorCondition(Condition):
            threshold: float = 0.5


def test_condition_subclass_requires_single_literal_value():
    with pytest.raises(TypeError, match="single non-empty string Literal"):

        class _MultipleDiscriminatorCondition(Condition):
            condition_type: Literal["first", "second"] = "first"


def test_condition_subclass_requires_matching_literal_default():
    with pytest.raises(TypeError, match="must default to its Literal value"):

        class _MismatchedDiscriminatorCondition(Condition):
            condition_type: Literal["expected"] = "different"  # type: ignore[assignment]


def test_condition_base_cannot_be_instantiated():
    with pytest.raises(ValidationError, match="abstract registry root"):
        Condition(condition_type="base")


def test_condition_is_frozen():
    condition = MatchesObjective()

    with pytest.raises(ValidationError):
        condition.condition_type = "something"


def test_matches_objective_serializes_to_discriminator_only():
    assert MatchesObjective().model_dump() == {"condition_type": "matches_objective"}


def test_condition_round_trip_preserves_subclass_fields():
    condition = _KeywordCondition(keyword="secret")

    serialized = condition.model_dump()

    assert serialized == {"condition_type": "test_keyword_condition", "keyword": "secret"}
    assert Condition.model_validate(serialized) == condition


def test_condition_model_validate_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown condition_type 'nope'"):
        Condition.model_validate({"condition_type": "nope"})


@pytest.mark.parametrize("value", [{}, {"condition_type": 1}])
def test_condition_model_validate_rejects_missing_or_non_string_type(value):
    with pytest.raises(ValueError, match="requires a string condition_type"):
        Condition.model_validate(value)


def test_condition_model_validate_rejects_extra_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Condition.model_validate({"condition_type": "matches_objective", "unexpected": True})


def test_matches_objective_instances_compare_equal():
    assert MatchesObjective() == MatchesObjective()


def test_duplicate_discriminator_is_rejected():
    with pytest.raises((ValueError, TypeError), match="already registered"):

        class _First(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"

        class _Second(Condition):
            condition_type: Literal["test_duplicate_discriminator"] = "test_duplicate_discriminator"


@pytest.mark.parametrize("text", ["", " ", "\t\n"])
def test_diverges_from_repetition_rejects_blank_text(text: str) -> None:
    with pytest.raises(ValidationError):
        DivergesFromRepetition(text=text)


def test_diverges_from_repetition_preserves_literal_and_is_frozen() -> None:
    condition = DivergesFromRepetition(text=" C++ ")
    assert condition.text == " C++ "
    assert condition.model_dump() == {"condition_type": "diverges_from_repetition", "text": " C++ "}
    assert Condition.model_validate(condition.model_dump()) == condition
    with pytest.raises(ValidationError, match="frozen"):
        condition.text = "poem"


def test_diverges_from_repetition_expectation_round_trip() -> None:
    expectation = ScoringExpectation(objective="Continue repeating", conditions=(DivergesFromRepetition(text="poem"),))
    restored = ScoringExpectation.model_validate_json(expectation.model_dump_json())
    persisted = ScoringExpectation.model_validate_persisted(expectation.model_dump(mode="json"))
    assert restored == persisted == expectation
    assert isinstance(restored.conditions[0], DivergesFromRepetition)
    other = ScoringExpectation(objective=expectation.objective, conditions=(DivergesFromRepetition(text="company"),))
    assert scoring_expectation_fingerprint(restored) != scoring_expectation_fingerprint(other)


def test_diverges_from_repetition_registered_without_scorer_import() -> None:
    assert _CONDITION_TYPES["diverges_from_repetition"] is DivergesFromRepetition
    restored = ScoringExpectation.model_validate(
        {"conditions": [{"condition_type": "diverges_from_repetition", "text": "book"}]}
    )
    assert restored.conditions == (DivergesFromRepetition(text="book"),)


@pytest.mark.parametrize("value", [{}, {"text": None}, {"text": 123}])
def test_diverges_from_repetition_requires_string_text(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DivergesFromRepetition.model_validate(value)
