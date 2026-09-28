# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.models import ComponentIdentifier
from pyrit.score.response_handler import JsonSchemaResponseHandler, TrueFalseResponseHandler

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


def test_json_schema_response_handler_rejects_category_in_both_by_default() -> None:
    handler = JsonSchemaResponseHandler()

    with pytest.raises(ValueError, match="Category is present in the response and an argument"):
        handler.parse(
            response_text='{"score_value": "1", "rationale": "r", "category": "violence"}',
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


def test_json_schema_response_handler_replay_identifier_unchanged_by_default() -> None:
    default = JsonSchemaResponseHandler()._get_replay_identifier()
    preferring = JsonSchemaResponseHandler(prefer_response_category=True)._get_replay_identifier()

    assert default is not None
    assert "prefer_response_category" not in default
    assert preferring is not None
    assert preferring["prefer_response_category"] is True
