# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.garak_system_prompt_dataset import (
    _GarakDrhSystemPromptDataset,
    _GarakTmSystemPromptDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def mock_drh_rows():
    return [
        {
            "systemprompt": "You are a helpful assistant.",
            "agentname": "Helper",
            "creation_date": "2024-01-01",
            "is-agent": True,
            "is-single-turn": False,
        },
        {"systemprompt": "You are a pirate.", "agentname": "Pirate"},
    ]


@pytest.fixture
def mock_tm_rows():
    return [
        {"prompt": "Act as a translator.", "id": "tm-1"},
        {"prompt": "Act as a poet.", "id": "tm-2"},
    ]


async def test_drh_fetch_maps_rows(mock_drh_rows):
    loader = _GarakDrhSystemPromptDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_drh_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 2
    assert all(isinstance(p, SeedPrompt) for p in dataset.seeds)
    first = dataset.seeds[0]
    assert first.value == "You are a helpful assistant."
    assert first.role == "system"
    assert first.dataset_name == "garak_drh_system_prompts"
    assert first.metadata["agentname"] == "Helper"
    assert first.metadata["creation_date"] == "2024-01-01"
    assert first.metadata["is_agent"] is True
    assert first.metadata["is_single_turn"] is False


async def test_drh_handles_missing_metadata_columns(mock_drh_rows):
    loader = _GarakDrhSystemPromptDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_drh_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert "creation_date" not in dataset.seeds[1].metadata
    assert dataset.seeds[1].metadata["agentname"] == "Pirate"


async def test_tm_fetch_maps_rows(mock_tm_rows):
    loader = _GarakTmSystemPromptDataset()

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=mock_tm_rows),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    first = dataset.seeds[0]
    assert first.value == "Act as a translator."
    assert first.role == "system"
    assert first.metadata["source_id"] == "tm-1"


async def test_empty_rows_raises():
    loader = _GarakTmSystemPromptDataset()

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


def test_dataset_names():
    assert _GarakDrhSystemPromptDataset().dataset_name == "garak_drh_system_prompts"
    assert _GarakTmSystemPromptDataset().dataset_name == "garak_tm_system_prompts"
