# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.equitymedqa_dataset import _EquityMedQADataset
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_equitymedqa_data():
    return [
        {
            "How do you calculate lung capacity?": "Some answer",
            "How do you calculate lung capacity for Black women?": "Another answer",
        },
    ]


async def test_fetch_dataset_single_subset(mock_equitymedqa_data):
    loader = _EquityMedQADataset(subset_name="cc_manual")

    with patch.object(loader, "_fetch_from_huggingface", new=AsyncMock(return_value=mock_equitymedqa_data)):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) > 0
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    assert all(p.harm_categories == ["health_bias"] for p in dataset.seeds)


async def test_fetch_dataset_multiple_subsets():
    loader = _EquityMedQADataset(subset_name=["cc_manual", "multimedqa"])

    mock_cc_manual_data = [
        {
            "How do you calculate lung capacity?": "answer1",
            "How do you calculate lung capacity for Black women?": "answer2",
        },
    ]
    mock_multimedqa_data = [
        {"Are benign brain tumors serious?": "answer3"},
    ]

    with patch.object(
        loader, "_fetch_from_huggingface", new=AsyncMock(side_effect=[mock_cc_manual_data, mock_multimedqa_data])
    ):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) > 0


def test_dataset_name():
    loader = _EquityMedQADataset()
    assert loader.dataset_name == "equitymedqa"


def test_invalid_subset_raises():
    with pytest.raises(ValueError, match="Invalid subset name"):
        _EquityMedQADataset(subset_name="nonexistent_subset")
