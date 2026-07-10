# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.score import CallableResponseHandler, JsonSchemaResponseHandler


def test_json_schema_handler_response_format_is_json():
    assert JsonSchemaResponseHandler().response_format == "json"


def test_callable_handler_response_format_is_none():
    # The escape hatch imposes no wire format so plain-text classifiers are not forced into JSON.
    assert CallableResponseHandler(parser=lambda _text: {}).response_format is None


def test_callable_response_handler_parses_dict_from_callable():
    handler = CallableResponseHandler(
        parser=lambda text: {"score_value": "True", "rationale": f"parsed:{text}", "metadata": "S1"}
    )

    score = handler.parse(
        response_text="unsafe",
        scorer_identifier=get_mock_target_identifier("Scorer"),
        scored_prompt_id="pid",
        category="harm",
    )

    assert score.raw_score_value == "True"
    assert score.score_rationale == "parsed:unsafe"
    assert score.score_category == ["harm"]
    assert score.score_metadata == {"metadata": "S1"}


def test_callable_response_handler_missing_required_key_raises_invalid_json():
    # score_value is required; its absence must raise InvalidJsonException so the retry applies.
    handler = CallableResponseHandler(parser=lambda _text: {"rationale": "r"})

    with pytest.raises(InvalidJsonException):
        handler.parse(
            response_text="x",
            scorer_identifier=get_mock_target_identifier("Scorer"),
            scored_prompt_id="pid",
        )


def test_callable_response_handler_wraps_parser_error_as_invalid_json():
    def boom(_text: str) -> dict[str, Any]:
        raise ValueError("unparseable")

    handler = CallableResponseHandler(parser=boom)

    with pytest.raises(InvalidJsonException):
        handler.parse(
            response_text="x",
            scorer_identifier=get_mock_target_identifier("Scorer"),
            scored_prompt_id="pid",
        )


def test_callable_response_handler_propagates_parser_invalid_json():
    def boom(_text: str) -> dict[str, Any]:
        raise InvalidJsonException(message="parser said retry")

    handler = CallableResponseHandler(parser=boom)

    with pytest.raises(InvalidJsonException, match="parser said retry"):
        handler.parse(
            response_text="x",
            scorer_identifier=get_mock_target_identifier("Scorer"),
            scored_prompt_id="pid",
        )


def test_callable_response_handler_rejects_category_in_both_response_and_argument():
    handler = CallableResponseHandler(
        parser=lambda _text: {"score_value": "True", "rationale": "r", "category": "from_response"}
    )

    with pytest.raises(ValueError, match="Category is present in the response and an argument"):
        handler.parse(
            response_text="x",
            scorer_identifier=get_mock_target_identifier("Scorer"),
            scored_prompt_id="pid",
            category="from_argument",
        )
