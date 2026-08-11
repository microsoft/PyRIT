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


def test_true_false_response_handler_rejects_value_outside_domain() -> None:
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    with pytest.raises(InvalidJsonException, match="must be 'true' or 'false'"):
        handler.parse(
            response_text='{"score_value": "refusal", "rationale": "test"}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )
