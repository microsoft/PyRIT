# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``parse_llamaguard_response``."""

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.score import parse_llamaguard_response


def test_parse_safe_response() -> None:
    result = parse_llamaguard_response("  Safe\n")

    assert result["score_value"] == "False"
    assert result["metadata"] == {"raw_classifier_output": "Safe"}


def test_parse_unsafe_response() -> None:
    result = parse_llamaguard_response("unsafe\nS1, S6, S11")

    assert result["score_value"] == "True"
    assert result["metadata"] == {
        "violated_categories": "S1,S6,S11",
        "raw_classifier_output": "unsafe\nS1, S6, S11",
    }
    assert "S1, S6, S11" in result["rationale"]


def test_parse_custom_policy_categories() -> None:
    result = parse_llamaguard_response(
        "unsafe\nCUSTOM_1,CUSTOM_2",
        allowed_categories={"CUSTOM_1", "CUSTOM_2"},
    )

    assert result["metadata"]["violated_categories"] == "CUSTOM_1,CUSTOM_2"


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   \n  ",
        "safe\nS1",
        "safe\nunsafe",
        "unsafe",
        "unsafe\n",
        "unsafe\nS1\nextra",
        "unsafe\nS1,S1",
        "unsafe\nS1,,S2",
        "unsafe\nS99",
        "I cannot classify this.",
        "safe.",
    ],
)
def test_parse_malformed_response_raises(response: str) -> None:
    with pytest.raises(InvalidJsonException):
        parse_llamaguard_response(response)
