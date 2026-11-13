# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``parse_nemotron_safety_guard_response``."""

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.models import NemotronSafetyGuardVerdicts
from pyrit.score import NemotronSafetyGuardLabel, parse_nemotron_safety_guard_response

UNSAFE_EXCHANGE = '{"User Safety": "unsafe", "Response Safety": "unsafe", "Safety Categories": "Violence, Threat"}'


@pytest.mark.parametrize(
    ("text", "label", "expected"),
    [
        (UNSAFE_EXCHANGE, NemotronSafetyGuardLabel.RESPONSE_SAFETY, "True"),
        ('{"User Safety": "unsafe", "Response Safety": "safe"}', NemotronSafetyGuardLabel.RESPONSE_SAFETY, "False"),
        ('{"User Safety": "unsafe", "Response Safety": "safe"}', NemotronSafetyGuardLabel.USER_SAFETY, "True"),
        ('{"User Safety": "safe", "Response Safety": "safe"}', NemotronSafetyGuardLabel.USER_SAFETY, "False"),
    ],
)
def test_selected_verdict_becomes_the_score_value(text: str, label: NemotronSafetyGuardLabel, expected: str) -> None:
    """One model call answers both verdicts, so the label picks without a second request."""
    assert parse_nemotron_safety_guard_response(text, label=label)["score_value"] == expected


def test_both_verdicts_and_categories_are_kept_in_metadata() -> None:
    parsed = parse_nemotron_safety_guard_response(UNSAFE_EXCHANGE, label=NemotronSafetyGuardLabel.USER_SAFETY)

    assert (
        parsed["metadata"]
        == NemotronSafetyGuardVerdicts(
            user_safety="unsafe", response_safety="unsafe", categories=("Violence", "Threat")
        ).to_metadata()
    )
    assert NemotronSafetyGuardVerdicts.from_metadata(metadata=parsed["metadata"]) == NemotronSafetyGuardVerdicts(
        user_safety="unsafe", response_safety="unsafe", categories=("Violence", "Threat")
    )
    assert (
        parsed["rationale"] == "Nemotron Safety Guard answered 'User Safety: unsafe' with categories: Violence, Threat."
    )


def test_category_names_keep_their_spelling() -> None:
    """The model answers with taxonomy names, which can contain slashes and spaces."""
    text = (
        '{"User Safety": "unsafe", "Response Safety": "unsafe", '
        '"Safety Categories": "Criminal Planning/Confessions, Guns and Illegal Weapons"}'
    )

    metadata = parse_nemotron_safety_guard_response(text)["metadata"]

    assert metadata[NemotronSafetyGuardVerdicts.CATEGORIES_KEY] == (
        "Criminal Planning/Confessions,Guns and Illegal Weapons"
    )


def test_safe_exchange_has_no_categories() -> None:
    parsed = parse_nemotron_safety_guard_response('{"User Safety": "safe", "Response Safety": "safe"}')

    assert NemotronSafetyGuardVerdicts.CATEGORIES_KEY not in parsed["metadata"]
    assert parsed["rationale"] == "Nemotron Safety Guard answered 'Response Safety: safe'."


def test_verdicts_are_read_case_insensitively() -> None:
    parsed = parse_nemotron_safety_guard_response('{"User Safety": " Safe", "Response Safety": "UNSAFE "}')

    assert parsed["score_value"] == "True"
    assert parsed["metadata"][NemotronSafetyGuardVerdicts.USER_SAFETY_KEY] == "safe"


def test_omitted_unselected_verdict_is_left_out() -> None:
    """The prompt tells the model to omit the response verdict when no response is present."""
    parsed = parse_nemotron_safety_guard_response(
        '{"User Safety": "unsafe"}', label=NemotronSafetyGuardLabel.USER_SAFETY
    )

    assert parsed["score_value"] == "True"
    assert "nemotron_safety_guard_response_safety" not in parsed["metadata"]


@pytest.mark.parametrize("label", list(NemotronSafetyGuardLabel))
def test_omitted_selected_verdict_raises(label: NemotronSafetyGuardLabel) -> None:
    """The scorer always sends a response, so an omitted verdict did not answer what was asked."""
    other = next(member for member in NemotronSafetyGuardLabel if member is not label)

    with pytest.raises(InvalidJsonException, match=f"omitted '{label.value}'"):
        parse_nemotron_safety_guard_response(f'{{"{other.value}": "safe"}}', label=label)


@pytest.mark.parametrize("text", ["", "   \n"])
def test_empty_response_raises(text: str) -> None:
    with pytest.raises(InvalidJsonException, match="empty"):
        parse_nemotron_safety_guard_response(text)


@pytest.mark.parametrize(
    "text",
    [
        "unsafe",
        '{"User Safety": "unsafe", "Response Safety": "unsafe"',
        '```json\n{"User Safety": "safe", "Response Safety": "safe"}\n```',
    ],
)
def test_text_that_is_not_json_raises(text: str) -> None:
    with pytest.raises(InvalidJsonException, match="invalid JSON"):
        parse_nemotron_safety_guard_response(text)


@pytest.mark.parametrize("text", ['["safe"]', '"safe"', "1"])
def test_json_that_is_not_an_object_raises(text: str) -> None:
    with pytest.raises(InvalidJsonException, match="not return a JSON object"):
        parse_nemotron_safety_guard_response(text)


@pytest.mark.parametrize(
    "text",
    [
        '{"User Safety": "unsafe", "Response Safety": "controversial"}',
        '{"User Safety": "unsafe", "Response Safety": true}',
        '{"User Safety": "maybe", "Response Safety": "safe"}',
    ],
)
def test_verdict_other_than_safe_or_unsafe_raises(text: str) -> None:
    with pytest.raises(InvalidJsonException, match="expected safe or unsafe"):
        parse_nemotron_safety_guard_response(text)


def test_non_text_categories_raise() -> None:
    text = '{"User Safety": "unsafe", "Response Safety": "unsafe", "Safety Categories": ["Violence"]}'

    with pytest.raises(InvalidJsonException, match="non-text 'Safety Categories'"):
        parse_nemotron_safety_guard_response(text)
