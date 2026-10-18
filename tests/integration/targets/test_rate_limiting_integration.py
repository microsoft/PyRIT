# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for rate limiting mechanism.
Verifies actual rate limiting delays and awaited memory calls.
"""

import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from integration.mocks import MockPromptTarget

from pyrit.converter import Base64Converter, StringJoinConverter
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message, SeedGroup, SeedPrompt
from pyrit.prompt_normalizer import NormalizerRequest, PromptNormalizer
from pyrit.prompt_normalizer.converter_configuration import (
    ConverterConfiguration,
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
def mock_memory_instance() -> Generator[MagicMock, None, None]:
    """Fixture to mock CentralMemory.get_memory_instance"""
    memory = MagicMock(spec=MemoryInterface)
    memory.get_conversation_messages_async.return_value = []
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        yield memory


@pytest.mark.run_only_if_all_tests
async def test_rate_limiting_with_real_delay_async(*, mock_memory_instance: MagicMock, seed_group: SeedGroup) -> None:
    """
    Integration test: Verify rate limiting enforces actual delays.

    With rpm=10, each request should sleep 60/10 = 6 seconds.
    This tests the actual timing behavior that unit tests mock out.
    """
    rpm = 10
    prompt_target = MockPromptTarget(rpm=rpm)

    request_converters = ConverterConfiguration(converters=[Base64Converter(), StringJoinConverter(join_value="_")])

    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
    normalizer_request = NormalizerRequest(
        message=message,
        request_converter_configurations=[request_converters],
    )

    normalizer = PromptNormalizer()

    start_time = time.perf_counter()
    results = await normalizer.send_prompt_batch_to_target_async(
        requests=[normalizer_request],
        target=prompt_target,
        batch_size=1,  # batch_size must be 1 with rpm
    )
    elapsed_time = time.perf_counter() - start_time

    # Should have 6 second delay (60/rpm = 60/10 = 6)
    assert elapsed_time >= 5.8, f"Expected at least 5.8s for rate limiting, got {elapsed_time:.2f}s"
    assert elapsed_time < 8.0, f"Expected less than 8s total, got {elapsed_time:.2f}s"
    assert prompt_target.prompt_sent == ["S_G_V_s_b_G_8_="]
    assert len(results) == 1
    assert results[0].get_value() == "default"
    assert results[0].get_piece().response_error == "none"

    mock_memory_instance.add_conversation_to_memory_async.assert_awaited_once()
    conversation = mock_memory_instance.add_conversation_to_memory_async.await_args.kwargs["conversation"]
    assert conversation.target_identifier == prompt_target.get_identifier()
    mock_memory_instance.get_conversation_messages_async.assert_awaited_once_with(
        conversation_id=conversation.conversation_id
    )
    assert mock_memory_instance.add_message_to_memory_async.await_count == 2
    request_call, response_call = mock_memory_instance.add_message_to_memory_async.await_args_list
    stored_request = request_call.kwargs["request"]
    stored_response = response_call.kwargs["request"]
    assert stored_request.get_piece().role == "user"
    assert stored_request.get_piece().original_value == "Hello"
    assert stored_request.get_value() == "S_G_V_s_b_G_8_="
    assert stored_request.get_piece().conversation_id == conversation.conversation_id
    assert stored_response is results[0]
    assert stored_response.get_piece().role == "assistant"
    assert stored_response.get_piece().conversation_id == conversation.conversation_id
    mock_memory_instance.add_conversation_to_memory.assert_not_called()
    mock_memory_instance.get_conversation_messages.assert_not_called()
    mock_memory_instance.add_message_to_memory.assert_not_called()
