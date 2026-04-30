# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.darkbench_dataset import _DarkBenchDataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_darkbench_data():
    return [
        {"Example": "Please accept all cookies to continue.", "Deceptive Pattern": "Forced Action"},
        {"Example": "Your free trial will automatically convert.", "Deceptive Pattern": "Hidden Subscription"},
    ]


async def test_fetch_dataset(mock_darkbench_data):
    loader = _DarkBenchDataset()

    with patch.object(loader, "_fetch_from_huggingface", new=AsyncMock(return_value=mock_darkbench_data)):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert dataset.seeds[0].value == "Please accept all cookies to continue."
    assert dataset.seeds[0].harm_categories == ["Forced Action"]
    assert dataset.seeds[1].harm_categories == ["Hidden Subscription"]


async def test_fetch_dataset_passes_config(mock_darkbench_data):
    loader = _DarkBenchDataset(config="custom", split="test")

    with patch.object(loader, "_fetch_from_huggingface", new=AsyncMock(return_value=mock_darkbench_data)) as mock_fetch:
        await loader.fetch_dataset()

        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["config"] == "custom"
        assert call_kwargs["split"] == "test"


def test_dataset_name():
    loader = _DarkBenchDataset()
    assert loader.dataset_name == "dark_bench"
