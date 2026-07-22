# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``parse_shieldgemma_response``."""

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.score import parse_shieldgemma_response


def test_parse_violation_verdict() -> None:
    result = parse_shieldgemma_response("Yes", guideline_name="No Dangerous Content")

    assert result["score_value"] == "True"
    assert result["metadata"] == {
        "guideline": "No Dangerous Content",
        "raw_classifier_output": "Yes",
    }


def test_parse_compliant_verdict() -> None:
    result = parse_shieldgemma_response("No", guideline_name="No Dangerous Content")

    assert result["score_value"] == "False"
    assert result["metadata"]["guideline"] == "No Dangerous Content"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("Yes, the request seeks instructions for building a weapon.", "True"),
        ("No. This is a benign factual question.", "False"),
        ("  yes\nStep 1: the content requests ...", "True"),
        ("NO, nothing here violates the principle.", "False"),
        ("**Yes** the content is dangerous.", "True"),
    ],
)
def test_parse_verdict_followed_by_reasoning(response: str, expected: str) -> None:
    # ShieldGemma is prompted to answer Yes or No and then explain, so trailing
    # reasoning must not be treated as a parse failure.
    assert parse_shieldgemma_response(response)["score_value"] == expected


def test_metadata_schema_is_stable_across_verdicts() -> None:
    violation = parse_shieldgemma_response("Yes", guideline_name="No Harassment")
    compliant = parse_shieldgemma_response("No", guideline_name="No Harassment")

    assert violation["metadata"].keys() == compliant["metadata"].keys()


def test_guideline_name_defaults_to_empty_string() -> None:
    assert parse_shieldgemma_response("No")["metadata"]["guideline"] == ""


def test_rationale_mentions_the_guideline_when_supplied() -> None:
    rationale = parse_shieldgemma_response("Yes", guideline_name="No Hate Speech")["rationale"]

    assert "No Hate Speech" in rationale


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   \n  ",
        "Maybe",
        "I cannot classify this.",
        "The content is unsafe.",
        "Affirmative",
    ],
)
def test_parse_malformed_response_raises(response: str) -> None:
    with pytest.raises(InvalidJsonException):
        parse_shieldgemma_response(response)
