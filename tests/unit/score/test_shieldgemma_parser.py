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
        "shieldgemma_no dangerous content_verdict": "Yes",
        "shieldgemma_no dangerous content_output": "Yes",
    }


def test_parse_compliant_verdict() -> None:
    result = parse_shieldgemma_response("No", guideline_name="No Dangerous Content")

    assert result["score_value"] == "False"
    assert result["metadata"]["shieldgemma_no dangerous content_verdict"] == "No"


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


def test_metadata_keys_fall_back_when_no_guideline_is_supplied() -> None:
    assert parse_shieldgemma_response("No")["metadata"] == {
        "shieldgemma_verdict": "No",
        "shieldgemma_output": "No",
    }


def test_metadata_keys_do_not_collide_across_guidelines() -> None:
    """
    TrueFalseCompositeScorer merges child metadata last-writer-wins, so one scorer per
    guideline has to produce disjoint keys or the aggregate names the wrong guideline.
    """
    dangerous = parse_shieldgemma_response("Yes, it explains how.", guideline_name="No Dangerous Content")
    hate = parse_shieldgemma_response("No. Benign.", guideline_name="No Hate Speech")

    assert not (dangerous["metadata"].keys() & hate["metadata"].keys())

    merged = {**dangerous["metadata"], **hate["metadata"]}
    assert merged["shieldgemma_no dangerous content_verdict"] == "Yes"
    assert merged["shieldgemma_no hate speech_verdict"] == "No"


@pytest.mark.parametrize("other_name", ["Hate-Speech", "Hate_Speech", "HateSpeech", "Hate  Speech"])
def test_metadata_keys_keep_names_the_policy_treats_as_distinct_apart(other_name: str) -> None:
    """
    ShieldGemmaPolicy enforces uniqueness on the case-folded name, so each of these can sit
    alongside "Hate Speech" in one policy. Any rewriting of the name is lossy and merges one
    of these pairs, reintroducing the collision this namespacing exists to prevent.
    """
    spaced = parse_shieldgemma_response("Yes", guideline_name="Hate Speech")
    other = parse_shieldgemma_response("No", guideline_name=other_name)

    assert not (spaced["metadata"].keys() & other["metadata"].keys())


def test_metadata_key_matches_the_policy_uniqueness_value() -> None:
    """Two names a policy rejects as duplicates should map to the same key."""
    upper = parse_shieldgemma_response("Yes", guideline_name="Hate Speech")
    lower = parse_shieldgemma_response("Yes", guideline_name="hate speech")

    assert upper["metadata"].keys() == lower["metadata"].keys()


def test_rationale_mentions_the_guideline_when_supplied() -> None:
    rationale = parse_shieldgemma_response("Yes", guideline_name="No Hate Speech")["rationale"]

    assert "No Hate Speech" in rationale


def test_rationale_preserves_the_model_explanation() -> None:
    """Attacks feed score_rationale back as model feedback, so the explanation must survive."""
    explanation = "the request asks for instructions to build an explosive device"

    rationale = parse_shieldgemma_response(f"Yes, {explanation}.", guideline_name="No Dangerous Content")["rationale"]

    assert explanation in rationale


def test_rationale_preserves_an_explanation_on_the_next_line() -> None:
    rationale = parse_shieldgemma_response("Yes\nStep 1: the content requests a weapon.")["rationale"]

    assert "Step 1: the content requests a weapon." in rationale


def test_rationale_falls_back_for_a_verdict_only_response() -> None:
    rationale = parse_shieldgemma_response("Yes", guideline_name="No Hate Speech")["rationale"]

    assert "the content violates it" in rationale


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
