# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset import (
    VLSUCategory,
    _VLSUMultimodalDataset,
)
from pyrit.memory import SQLiteMemory
from pyrit.memory.central_memory import CentralMemory
from pyrit.models import SeedDataset


class TestVLSUMultimodalDataset:
    """Unit tests for _VLSUMultimodalDataset."""

    @pytest.fixture(autouse=True)
    def setup_memory(self):
        """Set up memory instance for image downloads."""
        memory = SQLiteMemory()
        CentralMemory.set_memory_instance(memory)
        yield
        CentralMemory.set_memory_instance(None)

    def test_dataset_name(self):
        """Test that dataset_name property returns correct value."""
        dataset = _VLSUMultimodalDataset()
        assert dataset.dataset_name == "ml_vlsu"

    def test_init_with_categories(self):
        """Test initialization with category filtering."""
        categories = [VLSUCategory.SLURS_HATE_SPEECH, VLSUCategory.DISCRIMINATION]
        dataset = _VLSUMultimodalDataset(categories=categories)
        assert dataset.categories == categories

    def test_init_with_invalid_categories(self):
        """Test that invalid categories raise ValueError."""
        with pytest.raises(ValueError, match="Invalid VLSU categories"):
            _VLSUMultimodalDataset(categories=["invalid_category"])

    def test_init_with_unsafe_grades(self):
        """Test initialization with custom unsafe grades."""
        dataset = _VLSUMultimodalDataset(unsafe_grades=["unsafe"])
        assert dataset.unsafe_grades == ["unsafe"]

    @pytest.mark.asyncio
    async def test_fetch_dataset_combined_unsafe_creates_pair(self):
        """Test that text+image pair is created when combined_grade is unsafe."""
        test_uuid = str(uuid.uuid4())
        mock_data = [
            {
                "prompt": "Text that becomes unsafe with image",
                "web_path": "https://example.com/image.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C2: Discrimination and Unequal Treatment",
                "uuid": test_uuid,
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            }
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            assert isinstance(dataset, SeedDataset)
            assert len(dataset.seeds) == 2  # Text + Image pair

            # Find text and image prompts
            text_prompt = next(s for s in dataset.seeds if s.data_type == "text")
            image_prompt = next(s for s in dataset.seeds if s.data_type == "image_path")

            # Verify they share the same group_id
            assert text_prompt.prompt_group_id == image_prompt.prompt_group_id

            # Verify sequence order
            assert text_prompt.sequence == 0
            assert image_prompt.sequence == 1

            # Verify text prompt
            assert text_prompt.value == "Text that becomes unsafe with image"
            assert text_prompt.name == "ML-VLSU Text"
            assert text_prompt.harm_categories == ["C1: Slurs, Hate Speech, Hate Symbols"]
            assert text_prompt.metadata["text_grade"] == "safe"
            assert text_prompt.metadata["image_grade"] == "safe"
            assert text_prompt.metadata["combined_grade"] == "unsafe"

            # Verify image prompt
            assert image_prompt.value == "/fake/path/image.png"
            assert image_prompt.name == "ML-VLSU Image"
            assert image_prompt.harm_categories == ["C1: Slurs, Hate Speech, Hate Symbols"]
            assert image_prompt.metadata["original_image_url"] == "https://example.com/image.jpg"

    @pytest.mark.asyncio
    async def test_fetch_dataset_combined_borderline_creates_pair(self):
        """Test that text+image pair is created when combined_grade is borderline."""
        mock_data = [
            {
                "prompt": "Borderline text",
                "web_path": "https://example.com/image.jpg",
                "combined_category": "C3: Drug Abuse",
                "text_category": "C3: Drug Abuse",
                "image_category": "C3: Drug Abuse",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "borderline",
            }
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            assert len(dataset.seeds) == 2  # Text + Image pair

            text_prompt = next(s for s in dataset.seeds if s.data_type == "text")
            image_prompt = next(s for s in dataset.seeds if s.data_type == "image_path")

            assert text_prompt.prompt_group_id == image_prompt.prompt_group_id
            assert text_prompt.metadata["combined_grade"] == "borderline"

    @pytest.mark.asyncio
    async def test_fetch_dataset_combined_safe_no_prompts(self):
        """Test that no prompts are created when combined_grade is safe."""
        mock_data = [
            {
                "prompt": "Safe text",
                "web_path": "https://example.com/image.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "unsafe",  # Even if individual grades are unsafe
                "image_grade": "unsafe",
                "consensus_combined_grade": "safe",  # Combined is safe, so no prompts
            }
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await dataset_loader.fetch_dataset(cache=False)

    @pytest.mark.asyncio
    async def test_fetch_dataset_multiple_pairs(self):
        """Test that multiple text+image pairs are created correctly."""
        mock_data = [
            {
                "prompt": "First unsafe prompt",
                "web_path": "https://example.com/image1.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            },
            {
                "prompt": "Second unsafe prompt",
                "web_path": "https://example.com/image2.jpg",
                "combined_category": "C2: Discrimination and Unequal Treatment",
                "text_category": "C2: Discrimination and Unequal Treatment",
                "image_category": "C2: Discrimination and Unequal Treatment",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "borderline",
            },
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            assert len(dataset.seeds) == 4  # 2 pairs of text + image

            # Get unique group_ids
            group_ids = set(s.prompt_group_id for s in dataset.seeds)
            assert len(group_ids) == 2  # Two different pairs

            # Verify each pair has one text and one image
            for group_id in group_ids:
                pair = [s for s in dataset.seeds if s.prompt_group_id == group_id]
                assert len(pair) == 2
                data_types = {s.data_type for s in pair}
                assert data_types == {"text", "image_path"}

    @pytest.mark.asyncio
    async def test_category_filtering(self):
        """Test that category filtering works correctly."""
        mock_data = [
            {
                "prompt": "Slur prompt",
                "web_path": "https://example.com/image1.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            },
            {
                "prompt": "Discrimination prompt",
                "web_path": "https://example.com/image2.jpg",
                "combined_category": "C2: Discrimination and Unequal Treatment",
                "text_category": "C2: Discrimination and Unequal Treatment",
                "image_category": "C2: Discrimination and Unequal Treatment",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            },
        ]

        dataset_loader = _VLSUMultimodalDataset(categories=[VLSUCategory.SLURS_HATE_SPEECH])

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            # Only the slur category should be included (1 pair = 2 prompts)
            assert len(dataset.seeds) == 2
            for seed in dataset.seeds:
                assert "C1: Slurs" in str(seed.harm_categories)

    @pytest.mark.asyncio
    async def test_handles_failed_image_downloads(self):
        """Test that entire pair is skipped when image download fails."""
        mock_data = [
            {
                "prompt": "Test prompt",
                "web_path": "https://broken-url.com/image.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            }
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                side_effect=Exception("Download failed"),
            ),
        ):
            # Both text and image should be skipped when image fails
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await dataset_loader.fetch_dataset(cache=False)

    @pytest.mark.asyncio
    async def test_custom_unsafe_grades(self):
        """Test that custom unsafe_grades parameter works correctly."""
        mock_data = [
            {
                "prompt": "Unsafe prompt",
                "web_path": "https://example.com/image.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "image_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            },
            {
                "prompt": "Borderline prompt",
                "web_path": "https://example.com/image2.jpg",
                "combined_category": "C2: Discrimination and Unequal Treatment",
                "text_category": "C2: Discrimination and Unequal Treatment",
                "image_category": "C2: Discrimination and Unequal Treatment",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "borderline",
            },
        ]

        # Only include "unsafe", not "borderline"
        dataset_loader = _VLSUMultimodalDataset(unsafe_grades=["unsafe"])

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            # Only the "unsafe" pair should be included
            assert len(dataset.seeds) == 2
            for seed in dataset.seeds:
                assert seed.metadata["combined_grade"] == "unsafe"

    @pytest.mark.asyncio
    async def test_both_prompts_use_combined_category(self):
        """Test that both text and image prompts use the combined_category."""
        mock_data = [
            {
                "prompt": "Test prompt",
                "web_path": "https://example.com/image.jpg",
                "combined_category": "C1: Slurs, Hate Speech, Hate Symbols",
                "text_category": "C2: Discrimination and Unequal Treatment",
                "image_category": "C3: Drug Abuse",
                "uuid": str(uuid.uuid4()),
                "consensus_text_grade": "safe",
                "image_grade": "safe",
                "consensus_combined_grade": "unsafe",
            }
        ]

        dataset_loader = _VLSUMultimodalDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/image.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset(cache=False)

            # Both should use combined_category, not their individual categories
            for seed in dataset.seeds:
                assert seed.harm_categories == ["C1: Slurs, Hate Speech, Hate Symbols"]
