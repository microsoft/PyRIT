# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tagged size estimates reject ambiguous or contradictory states."""

import pytest
from pydantic import TypeAdapter, ValidationError

from pyrit.models import (
    AllAvailableDatasetSize,
    BoundedDatasetSize,
    DatasetLimitInput,
    DatasetLimitState,
    IndeterminateDatasetSize,
    ScenarioDatasetSizeEstimate,
)


@pytest.mark.parametrize(
    "size",
    [
        BoundedDatasetSize(value=0),
        BoundedDatasetSize(value=12),
        AllAvailableDatasetSize(),
        IndeterminateDatasetSize(),
    ],
)
def test_size_estimate_round_trip(size: ScenarioDatasetSizeEstimate) -> None:
    adapter = TypeAdapter(ScenarioDatasetSizeEstimate)
    assert adapter.validate_json(adapter.dump_json(size)) == size
    with pytest.raises(ValidationError, match="frozen"):
        size.kind = size.kind


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "bounded"},
        {"kind": "bounded", "value": None},
        {"kind": "bounded", "value": -1},
        {"kind": "bounded", "value": True},
        {"kind": "bounded", "value": 1.5},
        {"kind": "all_available", "value": 5},
        {"kind": "unbounded", "detail": "No limit"},
        {"kind": "indeterminate", "detail": ""},
        {"kind": "indeterminate", "reason": "configuration_unavailable"},
        {"kind": "partially_bounded"},
    ],
)
def test_size_estimate_rejects_invalid_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ScenarioDatasetSizeEstimate).validate_python(payload)


@pytest.mark.parametrize(
    ("state", "value"),
    [
        (DatasetLimitState.Value, None),
        (DatasetLimitState.Value, 0),
        (DatasetLimitState.ScenarioDefault, 5),
        (DatasetLimitState.NotApplicable, 5),
    ],
)
def test_dataset_limit_input_rejects_contradictory_values(*, state: DatasetLimitState, value: int | None) -> None:
    with pytest.raises(ValidationError):
        DatasetLimitInput(state=state, value=value)
