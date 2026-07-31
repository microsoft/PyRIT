# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``parse_wildguard_response``."""

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.score import WildGuardLabel, parse_wildguard_response

FULL_RESPONSE = "Harmful request: yes\nResponse refusal: no\nHarmful response: yes"


def test_parses_the_selected_label_as_the_score_value() -> None:
    parsed = parse_wildguard_response(FULL_RESPONSE, label=WildGuardLabel.HARMFUL_RESPONSE)

    assert parsed["score_value"] == "True"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (WildGuardLabel.HARMFUL_REQUEST, "True"),
        (WildGuardLabel.RESPONSE_REFUSAL, "False"),
        (WildGuardLabel.HARMFUL_RESPONSE, "True"),
    ],
)
def test_each_label_can_be_selected(label: WildGuardLabel, expected: str) -> None:
    """One model call answers all three, so the selector picks without a second request."""
    assert parse_wildguard_response(FULL_RESPONSE, label=label)["score_value"] == expected


def test_all_three_labels_are_reported_in_metadata() -> None:
    """The unselected judgements are kept so reading them costs no extra request."""
    metadata = parse_wildguard_response(FULL_RESPONSE, label=WildGuardLabel.RESPONSE_REFUSAL)["metadata"]

    assert metadata["selected_label"] == "Response refusal"
    assert metadata["harmful_request"] == "yes"
    assert metadata["response_refusal"] == "no"
    assert metadata["harmful_response"] == "yes"
    assert metadata["raw_classifier_output"] == FULL_RESPONSE


def test_parsing_is_case_insensitive_and_tolerates_extra_whitespace() -> None:
    text = "  harmful request:  YES  \n  RESPONSE REFUSAL: No\nharmful response:no  "

    parsed = parse_wildguard_response(text, label=WildGuardLabel.HARMFUL_REQUEST)

    assert parsed["score_value"] == "True"
    assert parsed["metadata"]["harmful_response"] == "no"


def test_na_is_accepted_for_an_unselected_label() -> None:
    """The paper documents N/A for the response-side labels when no response was supplied."""
    text = "Harmful request: yes\nResponse refusal: N/A\nHarmful response: N/A"

    parsed = parse_wildguard_response(text, label=WildGuardLabel.HARMFUL_REQUEST)

    assert parsed["score_value"] == "True"
    assert parsed["metadata"]["harmful_response"] == "n/a"


def test_na_on_the_selected_label_raises() -> None:
    """N/A has no true/false reading, so it cannot become a boolean score."""
    text = "Harmful request: yes\nResponse refusal: N/A\nHarmful response: N/A"

    with pytest.raises(InvalidJsonException, match="N/A"):
        parse_wildguard_response(text, label=WildGuardLabel.HARMFUL_RESPONSE)


def test_empty_response_raises() -> None:
    with pytest.raises(InvalidJsonException, match="empty"):
        parse_wildguard_response("   ")


@pytest.mark.parametrize(
    "text",
    [
        "Harmful request: yes\nResponse refusal: no",
        "Harmful request: yes",
        "The request looks harmful to me.",
    ],
)
def test_missing_labels_raise(text: str) -> None:
    with pytest.raises(InvalidJsonException, match="missing"):
        parse_wildguard_response(text)


def test_unexpected_label_value_raises() -> None:
    text = "Harmful request: maybe\nResponse refusal: no\nHarmful response: no"

    with pytest.raises(InvalidJsonException, match="expected yes, no, or N/A"):
        parse_wildguard_response(text)
