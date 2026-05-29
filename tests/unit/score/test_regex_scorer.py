# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.score import RegexScorer

_TEST_PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit Card": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}


async def test_regex_scorer_detects_match(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="SSN is 123-45-6789"))[0]
    assert score.get_value() is True
    assert "SSN" in score.score_rationale


async def test_regex_scorer_no_match(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="Nothing sensitive here."))[0]
    assert score.get_value() is False
    assert score.score_rationale == ""


async def test_regex_scorer_multiple_matches(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="SSN 123-45-6789 and card 4111-1111-1111-1111"))[0]
    assert score.get_value() is True
    assert "SSN" in score.score_rationale
    assert "Credit Card" in score.score_rationale


async def test_regex_scorer_categories_propagate(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS, categories=["pii"])
    score = (await scorer.score_text_async(text="SSN is 123-45-6789"))[0]
    assert "pii" in score.score_category


def test_regex_scorer_rejects_empty_patterns():
    with pytest.raises(ValueError, match="non-empty"):
        RegexScorer(patterns={})
