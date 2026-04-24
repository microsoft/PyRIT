# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for retry timing mechanism.
Verifies that exponential backoff retry delays work correctly with real timing.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from integration.mocks import MockPromptTarget

from pyrit.prompt_converter import TranslationConverter


@pytest.mark.asyncio
async def test_translation_converter_exponential_backoff_timing(sqlite_instance):
    """
    Integration test: Verify TranslationConverter uses exponential backoff with real delays.
    This tests the actual timing behavior that unit tests mock out.
    """
    prompt_target = MockPromptTarget()
    max_retries = 3
    translation_converter = TranslationConverter(
        converter_target=prompt_target,
        language="spanish",
        max_retries=max_retries,
        max_wait_time_in_seconds=10,  # Cap wait time
    )

    mock_send_prompt = AsyncMock(side_effect=Exception("Test failure"))

    start_time = time.time()
    with patch.object(prompt_target, "send_prompt_async", mock_send_prompt), pytest.raises(Exception):  # noqa: B017
        await translation_converter.convert_async(prompt="hello")
    elapsed_time = time.time() - start_time

    # With exponential backoff (multiplier=1, min=1): 1s + 2s = 3s minimum
    # Allow some tolerance for execution overhead
    assert elapsed_time >= 2.5, f"Expected at least 2.5s for exponential backoff, got {elapsed_time:.2f}s"
    assert elapsed_time < 6.0, f"Expected less than 6s total, got {elapsed_time:.2f}s"
    assert mock_send_prompt.call_count == max_retries
