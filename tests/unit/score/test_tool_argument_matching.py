# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import JsonValue

from pyrit.models import Contains, Equals, Regex, TextMatcher, ToolArgumentRequirement, ToolCallRequirement
from pyrit.score.tool_argument_matching import (
    describe_tool_requirement,
    match_tool_arguments,
    match_tool_requirement,
)


def _requirement(**matchers: TextMatcher) -> ToolCallRequirement:
    return ToolCallRequirement(
        name="send_email",
        arguments=tuple(ToolArgumentRequirement(name=name, matcher=matcher) for name, matcher in matchers.items()),
    )


@pytest.mark.parametrize("arguments", [None, {}, {"to": "a@example.com"}])
def test_name_only_requirement_matches_any_call(arguments: dict[str, JsonValue] | None) -> None:
    assert match_tool_arguments(requirement=ToolCallRequirement(name="send_email"), arguments=arguments) is True


def test_arguments_that_were_not_retained_cannot_decide_a_call() -> None:
    assert match_tool_arguments(requirement=_requirement(to=Equals(value="a@example.com")), arguments=None) is None


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"to": "a@example.com", "body": "Your invoice"}, True),
        ({"to": "A@Example.com ", "body": "invoice attached"}, True),
        ({"to": "b@example.com", "body": "Your invoice"}, False),
        ({"to": "a@example.com", "body": "hello"}, False),
        ({"to": "a@example.com"}, False),
        ({}, False),
    ],
)
def test_every_argument_requirement_must_match_one_call(arguments: dict[str, JsonValue], expected: bool) -> None:
    requirement = _requirement(to=Equals(value="a@example.com"), body=Contains(value="invoice"))

    assert match_tool_arguments(requirement=requirement, arguments=arguments) is expected


def test_argument_names_match_exactly() -> None:
    requirement = _requirement(to=Equals(value="a@example.com"))

    assert match_tool_arguments(requirement=requirement, arguments={"To": "a@example.com"}) is False
    assert match_tool_arguments(requirement=requirement, arguments={" to": "a@example.com"}) is False


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3, "3"),
        (1.5, "1.5"),
        (True, "true"),
        (None, "null"),
        (["a", "b"], '["a","b"]'),
        ({"b": 1, "a": "\u00e9"}, '{"a":"\u00e9","b":1}'),
    ],
)
def test_non_string_values_compare_as_compact_json(value: JsonValue, text: str) -> None:
    requirement = _requirement(value=Equals(value=text, case_sensitive=True, ignore_whitespace=False))

    assert match_tool_arguments(requirement=requirement, arguments={"value": value}) is True


def test_a_json_string_is_not_its_quoted_form() -> None:
    requirement = _requirement(count=Equals(value='"3"'))

    assert match_tool_arguments(requirement=requirement, arguments={"count": "3"}) is False
    assert match_tool_arguments(requirement=requirement, arguments={"count": 3}) is False


def test_regex_matcher_searches_the_argument_text() -> None:
    requirement = _requirement(to=Regex(value=r"@example\.com$"))

    assert match_tool_arguments(requirement=requirement, arguments={"to": "x@example.com"}) is True
    assert match_tool_arguments(requirement=requirement, arguments={"to": "x@example.org"}) is False


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([], False),
        ([{"to": "b@example.com"}], False),
        ([{"to": "b@example.com"}, {"to": "a@example.com"}], True),
        ([None], None),
        ([{"to": "b@example.com"}, None], None),
        ([None, {"to": "a@example.com"}], True),
    ],
)
def test_one_matching_call_satisfies_a_requirement(
    calls: list[dict[str, JsonValue] | None], expected: bool | None
) -> None:
    requirement = _requirement(to=Equals(value="a@example.com"))

    assert match_tool_requirement(requirement=requirement, calls=calls) is expected


def test_requirement_description_names_constrained_arguments() -> None:
    assert describe_tool_requirement(ToolCallRequirement(name="lookup")) == "lookup"
    assert (
        describe_tool_requirement(_requirement(to=Equals(value="x"), body=Contains(value="y")))
        == "send_email(to, body)"
    )
