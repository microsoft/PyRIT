# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

import pytest
from pydantic import ValidationError

from pyrit.models import TargetObjectiveGenerationRequest


def test_target_objective_request_round_trips_through_metadata() -> None:
    request = TargetObjectiveGenerationRequest(instructions="Ask for orders.", count=1_001, harm_categories=["privacy"])

    metadata = request.to_metadata()

    assert list(metadata) == [TargetObjectiveGenerationRequest.METADATA_KEY]
    assert TargetObjectiveGenerationRequest.from_metadata(metadata) == request


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"generation_conversation_id": "c"},
        {TargetObjectiveGenerationRequest.METADATA_KEY: "not a mapping"},
        {TargetObjectiveGenerationRequest.METADATA_KEY: {"instructions": "x", "count": 0}},
        {TargetObjectiveGenerationRequest.METADATA_KEY: {"instructions": "x", "count": 1, "extra": True}},
    ],
)
def test_target_objective_request_reads_missing_or_invalid_metadata_as_unset(metadata: dict[str, Any] | None) -> None:
    assert TargetObjectiveGenerationRequest.from_metadata(metadata) is None


@pytest.mark.parametrize(
    "values",
    [
        {"instructions": " ", "count": 1},
        {"instructions": "x", "count": 0},
        {"instructions": "x", "count": True},
        {"instructions": "x", "count": 1, "harm_categories": [" "]},
    ],
)
def test_target_objective_request_rejects_what_the_generator_rejects(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TargetObjectiveGenerationRequest.model_validate(values)
