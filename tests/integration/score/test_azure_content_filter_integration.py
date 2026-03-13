# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest
from integration.mocks import get_memory_interface

from pyrit.common.path import HOME_PATH
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.score import AzureContentFilterScorer


@pytest.fixture
def memory() -> Generator[MemoryInterface, None, None]:
    yield from get_memory_interface()


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_image_integration(memory) -> None:
    """
    Integration test for Azure Content Filter Scorer with image input.

    This test requires AZURE_CONTENT_SAFETY_API_KEY and AZURE_CONTENT_SAFETY_API_ENDPOINT
    environment variables to be set. Uses a sample image from the assets folder.
    """
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        # Verify required environment variables are set
        api_key = os.getenv("AZURE_CONTENT_SAFETY_API_KEY")
        endpoint = os.getenv("AZURE_CONTENT_SAFETY_API_ENDPOINT")

        if not api_key or not endpoint:
            pytest.skip("Azure Content Safety credentials not configured")

        scorer = AzureContentFilterScorer()

        image_path = HOME_PATH / "assets" / "architecture_components.png"
        scores = await scorer.score_image_async(image_path=str(image_path))

        # Should return scores for all harm categories
        assert len(scores) > 0, "Expected scores for harm categories"

        # Verify score structure
        for score in scores:
            assert score.score_type == "float_scale"
            assert score.score_category is not None
            assert 0.0 <= float(score.score_value) <= 1.0
            assert "azure_severity" in score.score_metadata

        # Architecture diagram should have low harm scores across all categories
        max_score = max(float(s.score_value) for s in scores)
        assert max_score < 0.5, "Architecture diagram should have low harm scores"


@pytest.mark.asyncio
async def test_azure_content_filter_scorer_long_text_chunking_integration(memory) -> None:
    """
    Integration test for Azure Content Filter Scorer with text longer than 10,000 characters.

    This verifies that the chunking and aggregation logic works correctly with the real API.
    """
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        # Verify required environment variables are set
        api_key = os.getenv("AZURE_CONTENT_SAFETY_API_KEY")
        endpoint = os.getenv("AZURE_CONTENT_SAFETY_API_ENDPOINT")

        if not api_key or not endpoint:
            pytest.skip("Azure Content Safety credentials not configured")

        scorer = AzureContentFilterScorer()

        # This should be greater than the rate limit
        base_text = "I hate you " * 10000

        assert len(base_text) > AzureContentFilterScorer.MAX_TEXT_LENGTH

        scores = await scorer.score_text_async(text=base_text)

        # Should return aggregated scores (one per category)
        assert len(scores) > 0, "Expected aggregated scores for harm categories"

        # Verify all scores are valid
        for score in scores:
            assert score.score_type == "float_scale"
            assert score.score_category is not None
            assert 0.0 <= float(score.score_value) <= 1.0
            assert "azure_severity" in score.score_metadata

        # Long benign text should still have low scores
        max_score = max(float(s.score_value) for s in scores)
        assert max_score > 0, "text should have > 0 score"
