# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Condition,
    Contains,
    DivergesFromRepetition,
    Equals,
    MatchesObjective,
    ScoringExpectation,
    ToolArgumentRequirement,
    ToolCall,
    ToolCallRequirement,
    ToolsCalled,
    scoring_expectation_fingerprint,
)
from pyrit.models import ToolCall as MessageToolCall
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


def test_tool_call_requirement_does_not_replace_message_protocol() -> None:
    assert ToolCall is MessageToolCall
    assert ToolCallRequirement is not ToolCall


def test_tools_called_round_trip_retains_exact_names() -> None:
    condition = ToolsCalled(
        tools=(
            ToolCallRequirement(name="lookup"),
            ToolCallRequirement(name="Lookup"),
            ToolCallRequirement(name=" lookup"),
        )
    )
    expectation = ScoringExpectation(conditions=(condition,))

    assert Condition.model_validate(condition.model_dump(mode="json")) == condition
    restored = ScoringExpectation.model_validate_persisted(expectation.model_dump(mode="json"))
    assert restored == expectation
    assert [tool.name for tool in condition.tools] == ["lookup", "Lookup", " lookup"]
    assert _CONDITION_TYPES["tools_called"] is ToolsCalled


@pytest.mark.parametrize("name", ["", " ", "\n\t", None, 42])
def test_tool_call_requirement_rejects_empty_or_non_string_name(name: object) -> None:
    with pytest.raises(ValidationError):
        ToolCallRequirement.model_validate({"name": name})


@pytest.mark.parametrize("names", [[], ["lookup", "lookup"]])
def test_tools_called_requires_nonempty_unique_names(names: list[str]) -> None:
    with pytest.raises(ValidationError, match="at least one tool|each tool name once"):
        ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names))


def test_subset_tools_called_keeps_its_stored_form_and_fingerprint() -> None:
    expectation = ScoringExpectation(
        objective="x", conditions=(ToolsCalled(tools=(ToolCallRequirement(name="lookup"),)), MatchesObjective())
    )

    assert expectation.conditions[0].model_dump(mode="json") == {
        "condition_type": "tools_called",
        "tools": [{"name": "lookup"}],
    }
    # Computed on main before ``exhaustive`` existed: stored subset expectations must still match.
    assert scoring_expectation_fingerprint(expectation) == (
        "09c38ed6d8ab39e973e8ffa1c30690079eaee0acd4df463665eaf3f62d8d482c"
    )


@pytest.mark.parametrize("names", [(), ("lookup",), ("lookup", "send")])
def test_exhaustive_tools_called_round_trips(names: tuple[str, ...]) -> None:
    condition = ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names), exhaustive=True)
    expectation = ScoringExpectation(conditions=(condition,))

    assert condition.model_dump(mode="json")["exhaustive"] is True
    assert Condition.model_validate(condition.model_dump(mode="json")) == condition
    assert ScoringExpectation.model_validate_persisted(expectation.model_dump(mode="json")) == expectation
    assert scoring_expectation_fingerprint(expectation) != scoring_expectation_fingerprint(
        ScoringExpectation(conditions=(condition.model_copy(update={"exhaustive": False}),))
    )


@pytest.mark.parametrize("value", [1, "true", "yes", None])
def test_exhaustive_flag_is_strict(value: object) -> None:
    with pytest.raises(ValidationError):
        ToolsCalled.model_validate({"tools": [{"name": "lookup"}], "exhaustive": value})


def test_explicit_false_exhaustive_flag_loads_as_a_subset_match() -> None:
    condition = Condition.model_validate(
        {"condition_type": "tools_called", "tools": [{"name": "lookup"}], "exhaustive": False}
    )

    assert condition == ToolsCalled(tools=(ToolCallRequirement(name="lookup"),))


@pytest.mark.parametrize("field", ["count", "order", "success"])
def test_tool_call_requirement_rejects_unsupported_matching_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ToolCallRequirement.model_validate({"name": "lookup", field: True})


def test_name_only_tool_requirements_keep_their_stored_form_and_fingerprint() -> None:
    expectation = ScoringExpectation(
        conditions=(ToolsCalled(tools=(ToolCallRequirement(name="send_email"), ToolCallRequirement(name="lookup"))),)
    )

    assert expectation.conditions[0].model_dump(mode="json") == {
        "condition_type": "tools_called",
        "tools": [{"name": "send_email"}, {"name": "lookup"}],
    }
    # Computed on main before ``arguments`` existed: stored name-only expectations must still match.
    assert scoring_expectation_fingerprint(expectation) == (
        "2f8d57c5e555a94728407772ef10e245c81902a4849d5576e7b0e9ddae4daab6"
    )


def test_explicit_empty_arguments_load_as_a_name_only_requirement() -> None:
    condition = Condition.model_validate(
        {"condition_type": "tools_called", "tools": [{"name": "lookup", "arguments": []}]}
    )

    assert condition == ToolsCalled(tools=(ToolCallRequirement(name="lookup"),))


def test_argument_requirements_round_trip_through_persistence() -> None:
    requirement = ToolCallRequirement(
        name="send_email",
        arguments=(
            ToolArgumentRequirement(name="to", matcher=Equals(value="a@example.com")),
            ToolArgumentRequirement(name="body", matcher=Contains(value="invoice", case_sensitive=True)),
        ),
    )
    condition = ToolsCalled(tools=(requirement,))
    expectation = ScoringExpectation(conditions=(condition,))

    assert condition.model_dump(mode="json")["tools"][0]["arguments"] == [
        {
            "name": "to",
            "matcher": {
                "value": "a@example.com",
                "case_sensitive": False,
                "ignore_whitespace": True,
                "matcher_type": "equals",
            },
        },
        {
            "name": "body",
            "matcher": {
                "value": "invoice",
                "case_sensitive": True,
                "ignore_whitespace": True,
                "matcher_type": "contains",
            },
        },
    ]
    assert Condition.model_validate(condition.model_dump(mode="json")) == condition
    assert ScoringExpectation.model_validate_persisted(expectation.model_dump(mode="json")) == expectation
    assert scoring_expectation_fingerprint(expectation) != scoring_expectation_fingerprint(
        ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="send_email"),)),))
    )


@pytest.mark.parametrize(
    "argument",
    [
        {"name": "", "matcher": {"matcher_type": "equals", "value": "x"}},
        {"name": 3, "matcher": {"matcher_type": "equals", "value": "x"}},
        {"name": "to"},
        {"name": "to", "matcher": {"matcher_type": "glob", "value": "x"}},
        {"name": "to", "matcher": {"matcher_type": "equals", "value": "x"}, "redacted": True},
    ],
)
def test_tool_argument_requirement_rejects_invalid_input(argument: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ToolCallRequirement.model_validate({"name": "send_email", "arguments": [argument]})


@pytest.mark.parametrize("arguments", [True, "to", {"to": "a@example.com"}])
def test_tool_call_requirement_rejects_unstructured_arguments(arguments: object) -> None:
    with pytest.raises(ValidationError):
        ToolCallRequirement.model_validate({"name": "lookup", "arguments": arguments})


def test_tools_called_schema_describes_argument_requirements() -> None:
    definitions = ScoringExpectation.model_json_schema()["$defs"]

    assert definitions["ToolCallRequirement"]["required"] == ["name"]
    assert definitions["ToolCallRequirement"]["properties"]["arguments"]["items"] == {
        "$ref": "#/$defs/ToolArgumentRequirement"
    }
    assert set(definitions["ToolArgumentRequirement"]["required"]) == {"name", "matcher"}


def test_tool_conditions_are_immutable() -> None:
    requirement = ToolCallRequirement(name="lookup")
    condition = ToolsCalled(tools=(requirement,))
    with pytest.raises(ValidationError, match="frozen"):
        requirement.name = "other"
    with pytest.raises(ValidationError, match="frozen"):
        condition.tools = ()


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
