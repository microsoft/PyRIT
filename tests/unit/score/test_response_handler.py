# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Sequence

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.models import ComponentIdentifier
from pyrit.score.response_handler import (
    CallableResponseHandler,
    JsonSchemaResponseHandler,
    NumericRangeResponseHandler,
    TrueFalseResponseHandler,
)

SCORER_IDENTIFIER = ComponentIdentifier(class_name="TestScorer", class_module=__name__)


@pytest.mark.parametrize("response_text", ["[]", '["score"]', '"true"', "1", "true", "null"])
def test_json_schema_response_handler_rejects_non_object_response(response_text: str) -> None:
    handler = JsonSchemaResponseHandler()

    with pytest.raises(InvalidJsonException, match="expected a top-level object"):
        handler.parse(
            response_text=response_text,
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("score_value", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_json_schema_response_handler_rejects_non_finite_numeric_values(score_value: str) -> None:
    handler = JsonSchemaResponseHandler(numeric_value=True)

    with pytest.raises(InvalidJsonException, match="finite float"):
        handler.parse(
            response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize(
    ("json_value", "expected"),
    [
        ("true", "true"),
        ("false", "false"),
        ('"true"', "true"),
        ('"false"', "false"),
    ],
)
def test_true_false_response_handler_accepts_boolean_values(json_value: str, expected: str) -> None:
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    score = handler.parse(
        response_text=f'{{"score_value": {json_value}, "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == expected


@pytest.mark.parametrize(
    ("json_value", "expected"),
    [
        ('"true "', "true"),
        ('" false"', "false"),
        ('"True\\n"', "true"),
        ('"  FALSE  "', "false"),
    ],
)
def test_true_false_response_handler_strips_whitespace_around_verdict(json_value: str, expected: str) -> None:
    # A judge returning a valid verdict with incidental surrounding whitespace
    # (e.g. a trailing newline) must not be rejected as out-of-domain.
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    score = handler.parse(
        response_text=f'{{"score_value": {json_value}, "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == expected


def test_true_false_response_handler_rejects_value_outside_domain() -> None:
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    with pytest.raises(InvalidJsonException, match="must be 'true' or 'false'"):
        handler.parse(
            response_text='{"score_value": "refusal", "rationale": "test"}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("response_category", ["harm", "violence"])
def test_json_schema_response_handler_rejects_category_in_both_by_default(response_category: str) -> None:
    handler = JsonSchemaResponseHandler()

    with pytest.raises(ValueError, match="Category is present in the response and an argument"):
        handler.parse(
            response_text=json.dumps({"score_value": "1", "rationale": "r", "category": response_category}),
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
            category="harm",
        )


def test_json_schema_response_handler_prefer_response_category() -> None:
    handler = JsonSchemaResponseHandler(prefer_response_category=True)

    from_response = handler.parse(
        response_text='{"score_value": "1", "rationale": "r", "category": "violence"}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category="harm",
    )
    fallback = handler.parse(
        response_text='{"score_value": "1", "rationale": "r"}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category="harm",
    )

    assert from_response.score_category == ["violence"]
    assert fallback.score_category == ["harm"]


def test_json_schema_response_handler_rejects_conflicting_category_preferences() -> None:
    with pytest.raises(ValueError, match="Only one category preference"):
        JsonSchemaResponseHandler(prefer_response_category=True, prefer_configured_category=True)


@pytest.mark.parametrize("category_output_key", ["category", "label"])
@pytest.mark.parametrize(
    ("response_category", "configured_category", "expected_category"),
    [
        ("violence", "harm", ["harm"]),
        ("harm", "harm", ["harm"]),
        (["violence", "privacy"], "harm", ["harm"]),
        (None, "harm", ["harm"]),
        ("violence", None, ["violence"]),
        (["violence", "privacy"], None, ["violence", "privacy"]),
        (None, None, None),
        ("violence", ["harm", "abuse"], ["harm", "abuse"]),
        ("violence", [], []),
        ("violence", "", [""]),
        (123, "harm", ["harm"]),
    ],
)
def test_json_schema_response_handler_prefers_configured_category(
    category_output_key: str,
    response_category: object,
    configured_category: Sequence[str] | str | None,
    expected_category: list[str] | None,
) -> None:
    handler = JsonSchemaResponseHandler(
        category_output_key=category_output_key,
        prefer_configured_category=True,
    )

    score = handler.parse(
        response_text=json.dumps({"score_value": "1", "rationale": "r", category_output_key: response_category}),
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category=configured_category,
    )

    assert score.score_category == expected_category


@pytest.mark.parametrize("response_category", [123, {"label": "harm"}, [123]])
def test_json_schema_response_handler_validates_response_category_without_configuration(
    response_category: object,
) -> None:
    handler = JsonSchemaResponseHandler(prefer_configured_category=True)

    with pytest.raises(InvalidJsonException, match="'category' must be"):
        handler.parse(
            response_text=json.dumps({"score_value": "1", "rationale": "r", "category": response_category}),
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


def test_json_schema_response_handler_replay_identifier_unchanged_by_default() -> None:
    default = JsonSchemaResponseHandler()._get_replay_identifier()
    response_first = JsonSchemaResponseHandler(prefer_response_category=True)._get_replay_identifier()
    configured_first = JsonSchemaResponseHandler(prefer_configured_category=True)._get_replay_identifier()

    assert default is not None
    assert "prefer_response_category" not in default
    assert "prefer_configured_category" not in default
    assert response_first == {**default, "prefer_response_category": True}
    assert configured_first == {**default, "prefer_configured_category": True}


@pytest.mark.parametrize("prefer_response_category", [False, True])
@pytest.mark.parametrize(
    ("response_text", "configured_category", "compatible"),
    [
        ('{"category": "violence"}', None, True),
        ("{}", "harm", True),
        ('{"category": null}', "harm", True),
        ('{"category": "violence"}', "harm", False),
        ('{"category": "harm"}', "harm", False),
        ('{"category": []}', "harm", False),
        ("[]", "harm", False),
        ("invalid json", "harm", False),
    ],
)
def test_json_schema_response_handler_legacy_contract_requires_unchanged_category_parsing(
    prefer_response_category: bool,
    response_text: str,
    configured_category: str | None,
    compatible: bool,
) -> None:
    handler = JsonSchemaResponseHandler(
        prefer_response_category=prefer_response_category,
        prefer_configured_category=not prefer_response_category,
    )

    legacy = handler._legacy_replay_identifiers(response_text=response_text, category=configured_category)

    expected = [JsonSchemaResponseHandler()._get_replay_identifier()] if compatible else []
    assert legacy == expected


@pytest.mark.parametrize("score_value", ["1", "5.5", "10"])
def test_numeric_range_response_handler_accepts_values_within_range(score_value: str) -> None:
    handler = NumericRangeResponseHandler(
        response_handler=JsonSchemaResponseHandler(numeric_value=True), minimum_value=1, maximum_value=10
    )

    score = handler.parse(
        response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == score_value


@pytest.mark.parametrize("score_value", ["0", "0.99", "10.01", "11", "-3"])
def test_numeric_range_response_handler_rejects_values_outside_range(score_value: str) -> None:
    handler = NumericRangeResponseHandler(
        response_handler=JsonSchemaResponseHandler(numeric_value=True), minimum_value=1, maximum_value=10
    )

    with pytest.raises(InvalidJsonException, match="must be between 1 and 10"):
        handler.parse(
            response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("score_value", ["high", "nan"])
def test_numeric_range_response_handler_rejects_non_numeric_from_wrapped_handler(score_value: str) -> None:
    # A caller-supplied wire-format handler may not validate numbers itself.
    handler = NumericRangeResponseHandler(
        response_handler=CallableResponseHandler(parser=lambda _: {"score_value": score_value, "rationale": "r"}),
        minimum_value=1,
        maximum_value=10,
    )

    with pytest.raises(InvalidJsonException):
        handler.parse(response_text="ignored", scorer_identifier=SCORER_IDENTIFIER, scored_prompt_id="test-id")


def test_numeric_range_response_handler_replay_identifier_includes_range() -> None:
    wrapped = JsonSchemaResponseHandler(numeric_value=True)
    handler = NumericRangeResponseHandler(response_handler=wrapped, minimum_value=1, maximum_value=10)

    identifier = handler._get_replay_identifier()

    assert identifier is not None
    assert identifier["wrapped"] == wrapped._get_replay_identifier()
    assert identifier["minimum_value"] == 1
    assert identifier["maximum_value"] == 10
    assert handler.json_response_config == wrapped.json_response_config
