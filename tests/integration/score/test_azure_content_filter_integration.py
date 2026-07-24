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

_AZURE_KEY_AUTH_DISABLED_REASON = "Azure key-based (local) auth is disabled in our tenant."


@pytest.fixture
def memory() -> Generator[MemoryInterface, None, None]:
    yield from get_memory_interface()


@pytest.fixture(
    params=[
        pytest.param(None, id="entra"),
        pytest.param(
            "AZURE_CONTENT_SAFETY_API_KEY",
            marks=[
                pytest.mark.run_only_if_all_tests,
                pytest.mark.skip(reason=_AZURE_KEY_AUTH_DISABLED_REASON),
            ],
            id="api-key",
        ),
    ]
)
def content_filter_scorer(request: pytest.FixtureRequest) -> AzureContentFilterScorer:
    api_key_env: str | None = request.param
    api_key = os.getenv(api_key_env) if api_key_env else None
    if api_key_env:
        assert api_key, f"{api_key_env} must be set to test API key auth"

    with patch.dict(os.environ, {"AZURE_CONTENT_SAFETY_API_KEY": ""}):
        return AzureContentFilterScorer(api_key=api_key)


async def test_azure_content_filter_scorer_image_integration(
    memory: MemoryInterface,
    content_filter_scorer: AzureContentFilterScorer,
) -> None:
    """
    Integration test for Azure Content Filter Scorer with image input.

    This test requires AZURE_CONTENT_SAFETY_API_ENDPOINT to be set.
    Uses a sample image from the assets folder.
    """
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        image_path = HOME_PATH / "assets" / "architecture_components.png"
        scores = await content_filter_scorer.score_image_async(image_path=str(image_path))

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


async def test_azure_content_filter_scorer_long_text_chunking_integration(
    memory: MemoryInterface,
    content_filter_scorer: AzureContentFilterScorer,
) -> None:
    """
    Integration test for Azure Content Filter Scorer with text longer than 10,000 characters.

    This verifies that the chunking and aggregation logic works correctly with the real API.
    """
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        # This should be greater than the rate limit
        base_text = "I hate you " * 10000

        assert len(base_text) > AzureContentFilterScorer.MAX_TEXT_LENGTH

        scores = await content_filter_scorer.score_text_async(text=base_text)

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
