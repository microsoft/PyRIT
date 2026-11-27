# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import (
    ComponentIdentifier,
    InvalidNemotronSafetyGuardVerdictsError,
    NemotronSafetyGuardVerdicts,
    Score,
)

KEYS = {
    "user_safety": NemotronSafetyGuardVerdicts.USER_SAFETY_KEY,
    "response_safety": NemotronSafetyGuardVerdicts.RESPONSE_SAFETY_KEY,
    "categories": NemotronSafetyGuardVerdicts.CATEGORIES_KEY,
}


def _score(metadata: dict | None) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=None,
        score_value_description="",
        score_rationale="",
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="NemotronSafetyGuardScorer", class_module="test"),
    )


def test_keys_are_namespaced():
    assert set(KEYS.values()) == {
        "nemotron_safety_guard_user_safety",
        "nemotron_safety_guard_response_safety",
        "nemotron_safety_guard_safety_categories",
    }


def test_round_trips_through_score_metadata():
    verdicts = NemotronSafetyGuardVerdicts(
        user_safety="unsafe", response_safety="safe", categories=("Violence", "Criminal Planning/Confessions")
    )

    assert verdicts.to_metadata() == {
        "nemotron_safety_guard_user_safety": "unsafe",
        "nemotron_safety_guard_response_safety": "safe",
        "nemotron_safety_guard_safety_categories": "Violence,Criminal Planning/Confessions",
    }
    assert NemotronSafetyGuardVerdicts.read(_score(verdicts.to_metadata())) == verdicts


@pytest.mark.parametrize(
    "verdicts",
    [
        NemotronSafetyGuardVerdicts(user_safety="safe"),
        NemotronSafetyGuardVerdicts(response_safety="unsafe"),
        NemotronSafetyGuardVerdicts(user_safety="safe", response_safety="safe"),
    ],
)
def test_omitted_parts_are_left_out_and_read_back_unset(verdicts):
    metadata = verdicts.to_metadata()

    assert set(metadata) == {
        key
        for field, key in KEYS.items()
        if (getattr(verdicts, field) if field != "categories" else verdicts.categories)
    }
    assert NemotronSafetyGuardVerdicts.from_metadata(metadata=metadata) == verdicts


def test_reading_keeps_other_metadata_out():
    metadata = {**NemotronSafetyGuardVerdicts(user_safety="safe", response_safety="safe").to_metadata(), "other": "x"}

    assert NemotronSafetyGuardVerdicts.from_metadata(metadata=metadata) == NemotronSafetyGuardVerdicts(
        user_safety="safe", response_safety="safe"
    )


@pytest.mark.parametrize("metadata", [None, {}, {"original_float_value": 0.5}, {KEYS["categories"]: "Violence"}])
def test_scores_without_verdicts_read_as_unset(metadata):
    assert NemotronSafetyGuardVerdicts.from_metadata(metadata=metadata) is None
    assert NemotronSafetyGuardVerdicts.read(_score(metadata)) is None


def test_reading_none_score_is_unset():
    assert NemotronSafetyGuardVerdicts.read(None) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_safety", "maybe"),
        ("user_safety", "Unsafe"),
        ("response_safety", 1),
        ("response_safety", ""),
        ("categories", 3),
    ],
)
def test_invalid_stored_verdicts_read_as_unset(field, value):
    stored = NemotronSafetyGuardVerdicts(user_safety="unsafe", response_safety="unsafe", categories=("Threat",))
    metadata = {**stored.to_metadata(), KEYS[field]: value}

    assert NemotronSafetyGuardVerdicts.from_metadata(metadata=metadata) is None


@pytest.mark.parametrize(
    "verdicts",
    [
        {},
        {"user_safety": "maybe"},
        {"user_safety": "safe", "response_safety": "SAFE"},
        {"user_safety": "unsafe", "categories": ("Violence, Threat",)},
        {"user_safety": "unsafe", "categories": (" ",)},
    ],
)
def test_construction_rejects_verdicts_outside_the_guards_answers(verdicts):
    with pytest.raises(InvalidNemotronSafetyGuardVerdictsError):
        NemotronSafetyGuardVerdicts(**verdicts)
