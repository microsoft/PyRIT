# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

import pytest
from pydantic import ValidationError

from pyrit.models import ScenarioDatasetPreparation


@pytest.mark.parametrize("pending", [True, False])
def test_scenario_dataset_preparation_metadata_round_trip(pending: bool) -> None:
    state = ScenarioDatasetPreparation(pending=pending)
    metadata = {"other": "kept", **json.loads(json.dumps(state.to_metadata()))}

    assert metadata == {"other": "kept", ScenarioDatasetPreparation.METADATA_KEY: {"pending": pending}}
    assert ScenarioDatasetPreparation.from_metadata(metadata=metadata) == state
    assert ScenarioDatasetPreparation.is_pending(metadata=metadata) is pending


@pytest.mark.parametrize("metadata", [{}, {"dataset_preparation_pending": True}])
def test_scenario_dataset_preparation_absent_state_reads_as_prepared(metadata: dict[str, Any]) -> None:
    assert ScenarioDatasetPreparation.from_metadata(metadata=metadata) is None
    assert ScenarioDatasetPreparation.is_pending(metadata=metadata) is False


@pytest.mark.parametrize("value", ["yes", 1, {}, {"pending": "true"}, {"pending": 1}, {"pending": True, "extra": 1}])
def test_scenario_dataset_preparation_rejects_malformed_state(value: Any) -> None:
    metadata = {ScenarioDatasetPreparation.METADATA_KEY: value}

    with pytest.raises(ValidationError):
        ScenarioDatasetPreparation.from_metadata(metadata=metadata)
    with pytest.raises(ValidationError):
        ScenarioDatasetPreparation.is_pending(metadata=metadata)
