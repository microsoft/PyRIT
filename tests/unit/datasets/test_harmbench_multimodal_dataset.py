# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.harmbench_multimodal_dataset import (
    SemanticCategory,
    _HarmBenchMultimodalDataset,
)
from pyrit.models import SeedDataset


@pytest.fixture
def mock_harmbench_mm_data():
    return [
        {
            "Behavior": "Describe harmful content in this image",
            "BehaviorID": "behavior_001",
            "FunctionalCategory": "multimodal",
            "SemanticCategory": "cybercrime_intrusion",
            "ImageFileName": "image1.jpg",
            "ImageDescription": "A test image",
            "RedactedImageDescription": "Redacted description",
        },
        {
            "Behavior": "Text-only behavior",
            "BehaviorID": "behavior_002",
            "FunctionalCategory": "standard",
            "SemanticCategory": "illegal",
            "ImageFileName": "image2.jpg",
        },
    ]


@pytest.mark.asyncio
async def test_fetch_dataset(mock_harmbench_mm_data):
    loader = _HarmBenchMultimodalDataset()

    with (
        patch.object(loader, "_fetch_from_url", return_value=mock_harmbench_mm_data),
        patch.object(loader, "_fetch_and_save_image_async", new=AsyncMock(return_value="/path/to/image.png")),
    ):
        dataset = await loader.fetch_dataset()

    assert isinstance(dataset, SeedDataset)
    # Only multimodal entry => 2 prompts (image + text)
    assert len(dataset.seeds) == 2
    image_prompt = dataset.seeds[0]
    text_prompt = dataset.seeds[1]
    assert image_prompt.data_type == "image_path"
    assert text_prompt.data_type == "text"
    assert text_prompt.value == "Describe harmful content in this image"


@pytest.mark.asyncio
async def test_fetch_dataset_skips_failed_images():
    data = [
        {
            "Behavior": "Describe harmful content",
            "BehaviorID": "b001",
            "FunctionalCategory": "multimodal",
            "SemanticCategory": "cybercrime_intrusion",
            "ImageFileName": "img1.jpg",
        },
        {
            "Behavior": "Another harmful behavior",
            "BehaviorID": "b002",
            "FunctionalCategory": "multimodal",
            "SemanticCategory": "illegal",
            "ImageFileName": "img2.jpg",
        },
    ]
    loader = _HarmBenchMultimodalDataset()

    with (
        patch.object(loader, "_fetch_from_url", return_value=data),
        patch.object(
            loader,
            "_fetch_and_save_image_async",
            new=AsyncMock(side_effect=[Exception("download failed"), "/path/to/image.png"]),
        ),
    ):
        dataset = await loader.fetch_dataset()

    # First image failed, second succeeded => 2 prompts (image + text for second)
    assert len(dataset.seeds) == 2


@pytest.mark.asyncio
async def test_fetch_dataset_filters_by_category():
    data = [
        {
            "Behavior": "Cybercrime behavior",
            "BehaviorID": "b001",
            "FunctionalCategory": "multimodal",
            "SemanticCategory": "cybercrime_intrusion",
            "ImageFileName": "img1.jpg",
        },
        {
            "Behavior": "Illegal behavior",
            "BehaviorID": "b002",
            "FunctionalCategory": "multimodal",
            "SemanticCategory": "illegal",
            "ImageFileName": "img2.jpg",
        },
    ]
    loader = _HarmBenchMultimodalDataset(categories=[SemanticCategory.ILLEGAL])

    with (
        patch.object(loader, "_fetch_from_url", return_value=data),
        patch.object(loader, "_fetch_and_save_image_async", new=AsyncMock(return_value="/path/to/image.png")),
    ):
        dataset = await loader.fetch_dataset()

    # Only "illegal" category matched => 2 prompts (image + text)
    assert len(dataset.seeds) == 2
    assert all(p.harm_categories == ["illegal"] for p in dataset.seeds)


@pytest.mark.asyncio
async def test_fetch_dataset_missing_keys_raises():
    loader = _HarmBenchMultimodalDataset()
    bad_data = [{"Behavior": "test"}]

    with patch.object(loader, "_fetch_from_url", return_value=bad_data):
        with pytest.raises(ValueError, match="Missing keys"):
            await loader.fetch_dataset()


def test_dataset_name():
    loader = _HarmBenchMultimodalDataset()
    assert loader.dataset_name == "harmbench_multimodal"


def test_init_with_invalid_categories_raises():
    """Test that invalid categories raise ValueError."""
    with pytest.raises(ValueError, match="Expected SemanticCategory"):
        _HarmBenchMultimodalDataset(categories=["not_a_category"])


def test_init_rejects_raw_string_matching_enum_value_for_categories():
    """Test that raw strings matching enum values are rejected."""
    with pytest.raises(ValueError, match="Expected SemanticCategory"):
        _HarmBenchMultimodalDataset(categories=["illegal"])
