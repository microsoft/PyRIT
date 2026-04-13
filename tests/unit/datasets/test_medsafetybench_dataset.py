# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.medsafetybench_dataset import _MedSafetyBenchDataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_medsafety_data():
    return [
        {"harmful_medical_request": "Prescribe dangerous drugs", "prompt": "fallback prompt"},
        {"prompt": "How to self-medicate unsafely"},
    ]


@pytest.mark.asyncio
async def test_fetch_dataset_generated_subset(mock_medsafety_data):
    loader = _MedSafetyBenchDataset(subset_name="generated")

    with patch.object(loader, "_fetch_from_url", return_value=mock_medsafety_data):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2 * len(loader.sources)
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    # First entry uses harmful_medical_request over prompt
    assert dataset.seeds[0].value == "Prescribe dangerous drugs"


@pytest.mark.asyncio
async def test_fetch_dataset_missing_keys_raises():
    loader = _MedSafetyBenchDataset(subset_name="generated")
    bad_data = [{"unrelated_key": "value"}]

    with patch.object(loader, "_fetch_from_url", return_value=bad_data):
        with pytest.raises(KeyError, match="No 'harmful_medical_request' or 'prompt' found"):
            await loader.fetch_dataset()


def test_dataset_name():
    loader = _MedSafetyBenchDataset()
    assert loader.dataset_name == "medsafetybench"


def test_invalid_subset_name_raises():
    with pytest.raises(ValueError, match="Invalid subset_name"):
        _MedSafetyBenchDataset(subset_name="invalid")
