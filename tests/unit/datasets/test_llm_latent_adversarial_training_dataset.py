# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.llm_latent_adversarial_training_dataset import (
    _LLMLatentAdversarialTrainingDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_lat_data():
    return [
        {"prompt": "How to bypass security?"},
        {"prompt": "Generate harmful content"},
    ]


@pytest.mark.asyncio
async def test_fetch_dataset(mock_lat_data):
    loader = _LLMLatentAdversarialTrainingDataset()

    with patch.object(loader, "_fetch_from_huggingface", new_callable=AsyncMock, return_value=mock_lat_data):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].value == "How to bypass security?"
    assert dataset.seeds[1].value == "Generate harmful content"


def test_dataset_name():
    loader = _LLMLatentAdversarialTrainingDataset()
    assert loader.dataset_name == "llm_lat_harmful"
