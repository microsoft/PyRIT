# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

import pytest
from pydantic import ValidationError

from pyrit.models import PromptGenerationEstimate, PromptGenerationStageEstimate


def _stage(**overrides: Any) -> PromptGenerationStageEstimate:
    return PromptGenerationStageEstimate.model_validate(
        {
            "stage": "seed_groups",
            "target_role": "generation",
            "min_calls": 1,
            "max_calls": 1,
            "min_tokens": 100,
            "max_tokens": 100,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_calls": 2, "max_calls": 1},
        {"min_tokens": 101, "max_tokens": 100},
        {"min_calls": -1},
        {"max_tokens": -1, "min_tokens": -2},
        {"min_calls": True},
        {"max_calls": "2"},
        {"stage": ""},
        {"target_role": ""},
        {"unexpected": 1},
    ],
)
def test_stage_estimate_rejects_invalid_bounds(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _stage(**overrides)


def test_stage_estimate_bounds_error_is_descriptive() -> None:
    with pytest.raises(ValidationError, match="at least the minimums"):
        _stage(min_calls=3, max_calls=2)


def test_stage_estimate_accepts_equal_and_zero_bounds() -> None:
    stage = _stage(min_calls=0, max_calls=0, min_tokens=0, max_tokens=0)
    assert stage.min_calls == stage.max_calls == 0


def test_stage_estimate_is_frozen() -> None:
    stage = _stage()
    with pytest.raises(ValidationError):
        stage.min_calls = 5  # type: ignore[misc]


def test_estimate_totals_sum_stages_and_apply_token_factors() -> None:
    estimate = PromptGenerationEstimate(
        stages=(
            _stage(stage="systematization", min_calls=1, max_calls=1, min_tokens=1000, max_tokens=1000),
            _stage(stage="seed_groups", min_calls=2, max_calls=6, min_tokens=1001, max_tokens=3003),
        ),
        exact_calls=False,
    )
    assert estimate.min_calls == 3
    assert estimate.max_calls == 7
    assert estimate.low_tokens == round(2001 * PromptGenerationEstimate.LOW_TOKEN_FACTOR)
    assert estimate.high_tokens == round(4003 * PromptGenerationEstimate.HIGH_TOKEN_FACTOR)
    assert estimate.low_tokens <= estimate.high_tokens


def test_estimate_without_stages_is_zero() -> None:
    estimate = PromptGenerationEstimate(exact_calls=True)
    assert (estimate.min_calls, estimate.max_calls, estimate.low_tokens, estimate.high_tokens) == (0, 0, 0, 0)
    assert estimate.notes == ()


def test_estimate_token_factors_bracket_the_stage_tokens() -> None:
    assert PromptGenerationEstimate.LOW_TOKEN_FACTOR < 1 < PromptGenerationEstimate.HIGH_TOKEN_FACTOR
    assert PromptGenerationEstimate.CHARACTERS_PER_TOKEN == 4


@pytest.mark.parametrize("characters, tokens", [(0, 0), (1, 1), (3, 1), (4, 1), (5, 2), (8, 2), (9, 3), (4001, 1001)])
def test_tokens_from_characters_rounds_up(characters: int, tokens: int) -> None:
    assert PromptGenerationEstimate.tokens_from_characters(characters) == tokens


def test_estimate_serialization_includes_computed_totals() -> None:
    estimate = PromptGenerationEstimate(
        stages=(_stage(min_calls=1, max_calls=2, min_tokens=100, max_tokens=200),),
        exact_calls=False,
        notes=("JSON validation retries can add calls.",),
    )
    data = json.loads(estimate.model_dump_json())
    assert data["min_calls"] == 1
    assert data["max_calls"] == 2
    assert data["low_tokens"] == 70
    assert data["high_tokens"] == 300
    assert data["exact_calls"] is False
    assert data["notes"] == ["JSON validation retries can add calls."]
    assert data["stages"] == [_stage(min_calls=1, max_calls=2, min_tokens=100, max_tokens=200).model_dump()]


def test_estimate_json_schema_lists_computed_totals_for_serialization() -> None:
    properties = PromptGenerationEstimate.model_json_schema(mode="serialization")["properties"]
    assert {"min_calls", "max_calls", "low_tokens", "high_tokens"} <= set(properties)


def test_estimate_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PromptGenerationEstimate.model_validate({"exact_calls": True, "unexpected": 1})


def _sample_estimate() -> PromptGenerationEstimate:
    return PromptGenerationEstimate(
        stages=(
            _stage(stage="systematization", min_tokens=1000, max_tokens=1000),
            _stage(min_calls=2, max_calls=6, min_tokens=1001, max_tokens=3003),
        ),
        exact_calls=False,
        notes=("JSON validation retries can add calls.",),
    )


def test_estimate_round_trips_through_its_own_json() -> None:
    estimate = _sample_estimate()
    restored = PromptGenerationEstimate.model_validate_json(estimate.model_dump_json())
    assert restored == estimate
    assert restored.model_dump() == estimate.model_dump()


def test_estimate_round_trips_through_its_own_dump() -> None:
    estimate = _sample_estimate()
    assert PromptGenerationEstimate.model_validate(estimate.model_dump()) == estimate
    assert PromptGenerationEstimate.model_validate(estimate.model_dump(mode="json")) == estimate


def test_estimate_recomputes_totals_supplied_on_input() -> None:
    estimate = PromptGenerationEstimate.model_validate(
        {
            "stages": [_stage(min_calls=1, max_calls=2, min_tokens=100, max_tokens=200).model_dump()],
            "exact_calls": False,
            "min_calls": 99,
            "max_calls": 99,
            "low_tokens": 99,
            "high_tokens": 99,
        }
    )
    assert (estimate.min_calls, estimate.max_calls, estimate.low_tokens, estimate.high_tokens) == (1, 2, 70, 300)


def test_estimate_accepts_model_instance_input() -> None:
    estimate = _sample_estimate()
    assert PromptGenerationEstimate.model_validate(estimate) == estimate
