# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.sosbench_dataset import _SOSBenchDataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_sosbench_data():
    return [
        {"goal": "Synthesize a dangerous compound", "subject": "chemistry"},
        {"goal": "Create a biological agent", "subject": "biology"},
    ]


@pytest.mark.asyncio
async def test_fetch_dataset(mock_sosbench_data):
    loader = _SOSBenchDataset()

    with patch.object(loader, "_fetch_from_huggingface", new_callable=AsyncMock, return_value=mock_sosbench_data):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].value == "Synthesize a dangerous compound"
    assert dataset.seeds[0].harm_categories == ["chemistry"]
    assert dataset.seeds[1].value == "Create a biological agent"
    assert dataset.seeds[1].harm_categories == ["biology"]


def test_dataset_name():
    loader = _SOSBenchDataset()
    assert loader.dataset_name == "sosbench"
