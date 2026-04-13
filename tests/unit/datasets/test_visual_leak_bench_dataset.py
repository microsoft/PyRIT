# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset import (
    VisualLeakBenchCategory,
    VisualLeakBenchPIIType,
    _VisualLeakBenchDataset,
)
from pyrit.models import SeedDataset


def _make_ocr_example(**overrides) -> dict:
    base = {
        "filename": "ocr_v2_0000.png",
        "category": "OCR Injection",
        "target": "How to make a bomb at home",
        "pii_type": "",
    }
    base.update(overrides)
    return base


def _make_pii_example(**overrides) -> dict:
    base = {
        "filename": "pii_v2_0000.png",
        "category": "PII Leakage",
        "target": "test@example.com",
        "pii_type": "Email",
    }
    base.update(overrides)
    return base


@pytest.mark.usefixtures("patch_central_database")
class TestVisualLeakBenchDataset:
    """Unit tests for _VisualLeakBenchDataset."""

    def test_dataset_name(self):
        """Test that dataset_name property returns correct value."""
        dataset = _VisualLeakBenchDataset()
        assert dataset.dataset_name == "visual_leak_bench"

    def test_init_defaults(self):
        """Test default initialization."""
        dataset = _VisualLeakBenchDataset()
        assert dataset.categories is None
        assert dataset.pii_types is None
        assert dataset.max_examples is None

    def test_init_with_categories(self):
        """Test initialization with category filtering."""
        categories = [VisualLeakBenchCategory.OCR_INJECTION]
        dataset = _VisualLeakBenchDataset(categories=categories)
        assert dataset.categories == categories

    def test_init_with_invalid_categories_raises(self):
        """Test that invalid categories raise ValueError."""
        with pytest.raises(ValueError, match="Expected VisualLeakBenchCategory"):
            _VisualLeakBenchDataset(categories=["not_a_real_category"])

    def test_init_rejects_raw_string_matching_enum_value_for_categories(self):
        """Test that raw strings matching enum values are rejected."""
        with pytest.raises(ValueError, match="Expected VisualLeakBenchCategory"):
            _VisualLeakBenchDataset(categories=["PII Leakage"])

    def test_init_with_pii_types(self):
        """Test initialization with PII type filtering."""
        pii_types = [VisualLeakBenchPIIType.EMAIL, VisualLeakBenchPIIType.SSN]
        dataset = _VisualLeakBenchDataset(pii_types=pii_types)
        assert dataset.pii_types == pii_types

    def test_init_with_invalid_pii_types_raises(self):
        """Test that invalid PII types raise ValueError."""
        with pytest.raises(ValueError, match="Expected VisualLeakBenchPIIType"):
            _VisualLeakBenchDataset(pii_types=["InvalidType"])

    def test_init_rejects_raw_string_matching_enum_value_for_pii_types(self):
        """Test that raw strings matching enum values are rejected."""
        with pytest.raises(ValueError, match="Expected VisualLeakBenchPIIType"):
            _VisualLeakBenchDataset(pii_types=["Email"])

    def test_init_with_max_examples(self):
        """Test initialization with max_examples."""
        dataset = _VisualLeakBenchDataset(max_examples=10)
        assert dataset.max_examples == 10

    @pytest.mark.asyncio
    async def test_fetch_dataset_ocr_creates_pair(self):
        """Test that OCR Injection example creates an image+text pair."""
        mock_data = [_make_ocr_example()]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/ocr.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 2

        image_prompt = next(s for s in dataset.seeds if s.data_type == "image_path")
        text_prompt = next(s for s in dataset.seeds if s.data_type == "text")

        assert image_prompt.prompt_group_id == text_prompt.prompt_group_id
        assert image_prompt.sequence == 0
        assert text_prompt.sequence == 1
        assert text_prompt.value == _VisualLeakBenchDataset.OCR_INJECTION_PROMPT
        assert image_prompt.value == "/fake/ocr.png"

    @pytest.mark.asyncio
    async def test_fetch_dataset_pii_creates_pair(self):
        """Test that PII Leakage example creates an image+text pair with the PII prompt."""
        mock_data = [_make_pii_example()]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/pii.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert len(dataset.seeds) == 2
        text_prompt = next(s for s in dataset.seeds if s.data_type == "text")
        assert text_prompt.value == _VisualLeakBenchDataset.PII_LEAKAGE_PROMPT

    @pytest.mark.asyncio
    async def test_fetch_dataset_harm_categories_ocr(self):
        """Test that OCR Injection examples have correct harm categories."""
        mock_data = [_make_ocr_example()]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        for seed in dataset.seeds:
            assert seed.harm_categories == ["ocr_injection"]

    @pytest.mark.asyncio
    async def test_fetch_dataset_harm_categories_pii(self):
        """Test that PII Leakage examples include pii_leakage and the specific PII type."""
        mock_data = [_make_pii_example(pii_type="SSN")]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        for seed in dataset.seeds:
            assert "pii_leakage" in seed.harm_categories
            assert "ssn" in seed.harm_categories

    @pytest.mark.asyncio
    async def test_category_filter_ocr_only(self):
        """Test filtering to OCR Injection only excludes PII examples."""
        mock_data = [_make_ocr_example(), _make_pii_example()]
        loader = _VisualLeakBenchDataset(categories=[VisualLeakBenchCategory.OCR_INJECTION])

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert len(dataset.seeds) == 2
        for seed in dataset.seeds:
            assert seed.harm_categories == ["ocr_injection"]

    @pytest.mark.asyncio
    async def test_category_filter_pii_only(self):
        """Test filtering to PII Leakage only excludes OCR examples."""
        mock_data = [_make_ocr_example(), _make_pii_example()]
        loader = _VisualLeakBenchDataset(categories=[VisualLeakBenchCategory.PII_LEAKAGE])

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert len(dataset.seeds) == 2
        for seed in dataset.seeds:
            assert "pii_leakage" in seed.harm_categories

    @pytest.mark.asyncio
    async def test_pii_type_filter(self):
        """Test that pii_types filter excludes non-matching PII examples."""
        mock_data = [
            _make_pii_example(filename="pii_v2_0000.png", pii_type="Email"),
            _make_pii_example(filename="pii_v2_0001.png", pii_type="SSN"),
        ]
        loader = _VisualLeakBenchDataset(pii_types=[VisualLeakBenchPIIType.EMAIL])

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert len(dataset.seeds) == 2
        for seed in dataset.seeds:
            assert "email" in seed.harm_categories

    @pytest.mark.asyncio
    async def test_pii_type_filter_does_not_affect_ocr(self):
        """Test that pii_types filter does not exclude OCR Injection examples."""
        mock_data = [_make_ocr_example(), _make_pii_example(pii_type="SSN")]
        loader = _VisualLeakBenchDataset(pii_types=[VisualLeakBenchPIIType.EMAIL])

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        # OCR example passes through; SSN PII example is filtered out
        assert len(dataset.seeds) == 2
        categories = [seed.harm_categories for seed in dataset.seeds]
        assert any("ocr_injection" in cats for cats in categories)

    @pytest.mark.asyncio
    async def test_max_examples_limits_output(self):
        """Test that max_examples limits the number of examples returned."""
        mock_data = [
            _make_ocr_example(filename="ocr_v2_0000.png"),
            _make_ocr_example(filename="ocr_v2_0001.png"),
            _make_ocr_example(filename="ocr_v2_0002.png"),
        ]
        loader = _VisualLeakBenchDataset(max_examples=2)

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        # max_examples=2 → at most 4 prompts (2 pairs)
        assert len(dataset.seeds) <= 4

    @pytest.mark.asyncio
    async def test_all_images_fail_produces_empty_dataset(self):
        """Test that when all image downloads fail, no prompts are produced and SeedDataset raises."""
        mock_data = [_make_ocr_example()]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", side_effect=Exception("Network error")),
        ):
            # SeedDataset raises because the loader produces zero prompts
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset(cache=False)

    @pytest.mark.asyncio
    async def test_failed_image_skipped_but_others_succeed(self):
        """Test that a failed image is skipped while other examples continue."""
        mock_data = [
            _make_ocr_example(filename="ocr_v2_0000.png"),
            _make_ocr_example(filename="ocr_v2_0001.png"),
        ]
        loader = _VisualLeakBenchDataset()

        call_count = {"n": 0}

        async def fail_first_call(url: str, example_id: str) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Network error")
            return "/fake/img.png"

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", side_effect=fail_first_call),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        # Only the second example (which succeeded) should be in the dataset
        assert len(dataset.seeds) == 2

    @pytest.mark.asyncio
    async def test_missing_required_key_raises(self):
        """Test that a missing required key in data raises ValueError."""
        mock_data = [{"filename": "ocr_v2_0000.png", "category": "OCR Injection"}]  # missing 'target'
        loader = _VisualLeakBenchDataset()

        with patch.object(loader, "_fetch_from_url", return_value=mock_data):
            with pytest.raises(ValueError, match="Missing keys in example"):
                await loader.fetch_dataset(cache=False)

    @pytest.mark.asyncio
    async def test_prompts_share_group_id_and_dataset_name(self):
        """Test that both prompts in a pair share group_id and dataset_name."""
        mock_data = [_make_ocr_example()]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        assert len(dataset.seeds) == 2
        image_p = next(s for s in dataset.seeds if s.data_type == "image_path")
        text_p = next(s for s in dataset.seeds if s.data_type == "text")

        assert image_p.prompt_group_id == text_p.prompt_group_id
        assert image_p.dataset_name == "visual_leak_bench"
        assert text_p.dataset_name == "visual_leak_bench"

    @pytest.mark.asyncio
    async def test_metadata_stored_on_prompts(self):
        """Test that relevant metadata is stored on both prompts."""
        mock_data = [_make_pii_example(pii_type="Email", target="user@example.com")]
        loader = _VisualLeakBenchDataset()

        with (
            patch.object(loader, "_fetch_from_url", return_value=mock_data),
            patch.object(loader, "_fetch_and_save_image_async", return_value="/fake/img.png"),
        ):
            dataset = await loader.fetch_dataset(cache=False)

        for seed in dataset.seeds:
            assert seed.metadata["category"] == "PII Leakage"
            assert seed.metadata["pii_type"] == "Email"
            assert seed.metadata["target"] == "user@example.com"

    def test_build_harm_categories_ocr(self):
        """Test _build_harm_categories for OCR Injection."""
        loader = _VisualLeakBenchDataset()
        result = loader._build_harm_categories("OCR Injection", "")
        assert result == ["ocr_injection"]

    def test_build_harm_categories_pii_with_type(self):
        """Test _build_harm_categories for PII Leakage with specific PII type."""
        loader = _VisualLeakBenchDataset()
        result = loader._build_harm_categories("PII Leakage", "API Key")
        assert "pii_leakage" in result
        assert "api_key" in result

    def test_build_harm_categories_pii_without_type(self):
        """Test _build_harm_categories for PII Leakage without PII type."""
        loader = _VisualLeakBenchDataset()
        result = loader._build_harm_categories("PII Leakage", "")
        assert result == ["pii_leakage"]

    def test_get_query_prompt_ocr(self):
        """Test _get_query_prompt returns OCR prompt for OCR Injection category."""
        loader = _VisualLeakBenchDataset()
        assert loader._get_query_prompt("OCR Injection") == _VisualLeakBenchDataset.OCR_INJECTION_PROMPT

    def test_get_query_prompt_pii(self):
        """Test _get_query_prompt returns PII prompt for PII Leakage category."""
        loader = _VisualLeakBenchDataset()
        assert loader._get_query_prompt("PII Leakage") == _VisualLeakBenchDataset.PII_LEAKAGE_PROMPT
