# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``parse_llamaguard_response``."""

import pytest

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.score import parse_llamaguard_response


def test_parse_safe_response() -> None:
    result = parse_llamaguard_response("safe")
    assert result["score_value"] == "False"
    assert "safe" in result["description"].lower()
    assert result["metadata"]["raw_classifier_output"] == "safe"


def test_parse_safe_with_surrounding_whitespace() -> None:
    result = parse_llamaguard_response("  safe\n")
    assert result["score_value"] == "False"


def test_parse_safe_case_insensitive() -> None:
    result = parse_llamaguard_response("Safe")
    assert result["score_value"] == "False"


def test_parse_unsafe_with_single_category() -> None:
    result = parse_llamaguard_response("unsafe\nS6")
    assert result["score_value"] == "True"
    assert result["metadata"]["violated_categories"] == "S6"
    assert "S6" in result["rationale"]


def test_parse_unsafe_with_multiple_categories() -> None:
    result = parse_llamaguard_response("unsafe\nS1,S6,S11")
    assert result["score_value"] == "True"
    assert result["metadata"]["violated_categories"] == "S1,S6,S11"


def test_parse_unsafe_with_spaces_between_categories() -> None:
    result = parse_llamaguard_response("unsafe\nS1, S6, S11")
    assert result["score_value"] == "True"
    # Whitespace stripped from each code
    assert result["metadata"]["violated_categories"] == "S1,S6,S11"


def test_parse_unsafe_without_categories() -> None:
    # Some LlamaGuard outputs omit the category line entirely
    result = parse_llamaguard_response("unsafe")
    assert result["score_value"] == "True"
    assert result["metadata"]["violated_categories"] == ""
    assert "no categories reported" in result["rationale"].lower()


def test_parse_unsafe_with_empty_category_line() -> None:
    # Trailing newline with nothing after
    result = parse_llamaguard_response("unsafe\n")
    assert result["score_value"] == "True"
    assert result["metadata"]["violated_categories"] == ""


def test_parse_empty_response_raises() -> None:
    with pytest.raises(InvalidJsonException):
        parse_llamaguard_response("")


def test_parse_whitespace_only_response_raises() -> None:
    with pytest.raises(InvalidJsonException):
        parse_llamaguard_response("   \n  ")


def test_parse_refusal_or_unrecognized_verdict_raises() -> None:
    # If LlamaGuard emits a refusal or some other prefix, retry by raising
    with pytest.raises(InvalidJsonException):
        parse_llamaguard_response("I cannot help with that.")


def test_parse_verdict_with_trailing_punctuation_raises() -> None:
    # Strict format expected. The retry layer handles transient deviations.
    with pytest.raises(InvalidJsonException):
        parse_llamaguard_response("safe.")
