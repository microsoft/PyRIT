# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for rate limiting mechanism.
Verifies that actual rate limiting delays work correctly with real timing.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from integration.mocks import MockPromptTarget

from pyrit.memory import CentralMemory
from pyrit.models import Message, SeedGroup, SeedPrompt
from pyrit.prompt_converter import Base64Converter, StringJoinConverter
from pyrit.prompt_normalizer import NormalizerRequest, PromptNormalizer
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)


@pytest.fixture
def seed_group() -> SeedGroup:
    return SeedGroup(
        seeds=[
            SeedPrompt(
                value="Hello",
                data_type="text",
                role="system",
                sequence=1,
            )
        ]
    )


@pytest.fixture
def mock_memory_instance():
    """Fixture to mock CentralMemory.get_memory_instance"""
    memory = MagicMock()
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        yield memory


@pytest.mark.asyncio
async def test_rate_limiting_with_real_delay(mock_memory_instance, seed_group):
    """
    Integration test: Verify rate limiting enforces actual delays.

    With rpm=10, each request should sleep 60/10 = 6 seconds.
    This tests the actual timing behavior that unit tests mock out.
    """
    rpm = 10
    prompt_target = MockPromptTarget(rpm=rpm)

    request_converters = PromptConverterConfiguration(
        converters=[Base64Converter(), StringJoinConverter(join_value="_")]
    )

    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
    normalizer_request = NormalizerRequest(
        message=message,
        request_converter_configurations=[request_converters],
    )

    normalizer = PromptNormalizer()

    start_time = time.time()
    results = await normalizer.send_prompt_batch_to_target_async(
        requests=[normalizer_request],
        target=prompt_target,
        batch_size=1,  # batch_size must be 1 with rpm
    )
    elapsed_time = time.time() - start_time

    # Should have 6 second delay (60/rpm = 60/10 = 6)
    assert elapsed_time >= 5.8, f"Expected at least 5.8s for rate limiting, got {elapsed_time:.2f}s"
    assert elapsed_time < 8.0, f"Expected less than 8s total, got {elapsed_time:.2f}s"
    assert "S_G_V_s_b_G_8_=" in prompt_target.prompt_sent
    assert len(results) == 1
