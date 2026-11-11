# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import codecs
import logging
import os
import tempfile
import threading
import wave
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from unit.async_utils import (
    TEST_WATCHDOG_SECONDS,
    drain_tasks_async,
    wait_for_completion_async,
    wait_for_event_async,
    wait_for_worker_ready_async,
)
from unit.mocks import MockPromptTarget, get_image_message_piece, get_mock_attack_identifier, get_mock_target_identifier

from pyrit.converter import (
    Base64Converter,
    Converter,
    ConverterResult,
    ROT13Converter,
    SelectiveTextConverter,
    StringJoinConverter,
    SuffixAppendConverter,
    TokenSelectionStrategy,
)
from pyrit.exceptions import (
    ComponentRole,
    EmptyResponseException,
    clear_execution_context,
    execution_context,
    get_execution_context,
)
from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.message_normalizer import MessageListNormalizer
from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedGroup,
    SeedPrompt,
)
from pyrit.prompt_normalizer import (
    ConverterConfiguration,
    NormalizerRequest,
    PromptNormalizer,
)
from pyrit.prompt_normalizer import prompt_normalizer as prompt_normalizer_module
from pyrit.prompt_target import CapabilityName, PromptTarget, TargetCapabilities, TargetConfiguration


@pytest.fixture
def response() -> Message:
    conversation_id = "123"
    image_message_piece = get_image_message_piece()
    image_message_piece.role = "assistant"
    image_message_piece.conversation_id = conversation_id
    return Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="Hello", conversation_id=conversation_id),
            MessagePiece(role="assistant", original_value="part 2", conversation_id=conversation_id),
            image_message_piece,
        ]
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
    memory = MagicMock(spec=MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        yield memory


class MockConverter(Converter):
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    def __init__(self) -> None:
        pass

    def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:  # type: ignore[arg-type]
        return ConverterResult(output_text=prompt, output_type="text")

    def input_supported(self, input_type: PromptDataType) -> bool:
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        return output_type == "text"


class ContextFailingConverter(Converter):
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        raise ValueError("conversion failed")


@pytest.mark.parametrize(("start_token", "end_token"), [("", "⟫"), ("⟪", ""), ("", "")])
def test_prompt_normalizer_rejects_empty_markers_before_memory_lookup(*, start_token: str, end_token: str) -> None:
    with patch.object(CentralMemory, "get_memory_instance") as memory:
        with pytest.raises(ValueError, match="tokens must be non-empty"):
            PromptNormalizer(start_token=start_token, end_token=end_token)
    memory.assert_not_called()


def assert_message_piece_hashes_set(request: Message):
    assert request
    assert request.message_pieces
    for piece in request.message_pieces:
        assert piece.original_value_sha256
        assert piece.converted_value_sha256


@pytest.mark.parametrize("split_configurations", [False, True])
async def test_convert_values_nested_markers_async(
    *, mock_memory_instance: MagicMock, split_configurations: bool
) -> None:
    converters = [Base64Converter(), ROT13Converter(), SuffixAppendConverter(suffix="tail")]
    configurations = (
        [ConverterConfiguration(converters=[converter]) for converter in converters]
        if split_configurations
        else [ConverterConfiguration(converters=converters)]
    )
    original = "keep ⟪⟪test⟫⟫ and ⟪⟪test2⟫⟫"
    message = Message.from_prompt(prompt=original, role="user")
    await PromptNormalizer().convert_values_async(converter_configurations=configurations, message=message)

    assert message.message_pieces[0].original_value == original
    assert message.message_pieces[0].converted_value == (
        f"keep {codecs.encode('dGVzdA==', 'rot_13')} and {codecs.encode('dGVzdDI=', 'rot_13')} tail"
    )
    assert message.message_pieces[0].converted_value_data_type == "text"
    assert mock_memory_instance.mock_calls == []


@pytest.mark.parametrize("preserve_tokens", [False, True])
@pytest.mark.parametrize(("start_token", "end_token"), [("⟪", "⟫"), ("<<", ">>")])
async def test_convert_values_token_selection_matches_direct_async(
    *, mock_memory_instance: MagicMock, preserve_tokens: bool, start_token: str, end_token: str
) -> None:
    converter = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=preserve_tokens,
        start_token=start_token,
        end_token=end_token,
    )
    prompt = f"keep {start_token}{start_token}test{end_token}{end_token} / {start_token}test2{end_token}"
    direct = await converter.convert_async(prompt=prompt)
    message = Message.from_prompt(prompt=prompt, role="user")
    normalizer = PromptNormalizer(start_token=start_token, end_token=end_token)
    await normalizer.convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=[converter])], message=message
    )
    expected = (
        f"keep {start_token}{start_token}dGVzdA=={end_token}{end_token} / {start_token}dGVzdDI={end_token}"
        if preserve_tokens
        else f"keep {start_token}dGVzdA=={end_token} / dGVzdDI="
    )
    assert direct.output_text == message.message_pieces[0].converted_value == expected
    assert message.message_pieces[0].original_value == prompt
    assert mock_memory_instance.mock_calls == []


async def test_send_prompt_async_multiple_converters(mock_memory_instance, seed_group):
    prompt_target = MockPromptTarget()
    request_converters = [ConverterConfiguration(converters=[Base64Converter(), StringJoinConverter(join_value="_")])]

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    await normalizer.send_prompt_async(
        message=message, request_converter_configurations=request_converters, target=prompt_target
    )

    assert prompt_target.prompt_sent == ["S_G_V_s_b_G_8_="]


async def test_send_prompt_async_forwards_normalizer_overrides_and_context(mock_memory_instance):
    prompt_target = MagicMock(spec=PromptTarget)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(
        return_value=[MessagePiece(role="assistant", original_value="first").to_message()]
    )
    normalizer = PromptNormalizer()
    conversation_id = "prepended-conversation"
    message_normalizer = MagicMock(spec=MessageListNormalizer)
    normalizer_overrides = {CapabilityName.EDITABLE_HISTORY: message_normalizer}
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(uuid4(),),
        replay_seed_each_send=False,
    )

    await normalizer.send_prompt_async(
        message=Message.from_prompt(prompt="first request", role="user"),
        target=prompt_target,
        conversation_id=conversation_id,
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    call = prompt_target.send_prompt_async.await_args
    assert call.kwargs["normalizer_overrides"] == normalizer_overrides
    assert call.kwargs["send_context"] is target_context


async def test_send_prompt_async_conversion_failure_does_not_call_target(mock_memory_instance):
    prompt_target = MagicMock(spec=PromptTarget)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock()
    conversation_id = "prepended-conversation"
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(uuid4(),),
        replay_seed_each_send=False,
    )
    converter_config = ConverterConfiguration.from_converters(converters=[ContextFailingConverter()])

    with pytest.raises(ValueError, match="conversion failed"):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"),
            target=prompt_target,
            conversation_id=conversation_id,
            request_converter_configurations=converter_config,
            send_context=target_context,
        )

    prompt_target.send_prompt_async.assert_not_awaited()
    mock_memory_instance.add_message_to_memory_async.assert_not_called()


async def test_send_prompt_async_target_failure_is_persisted(mock_memory_instance):
    prompt_target = MagicMock(spec=PromptTarget)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(side_effect=ValueError("normalization failed"))
    conversation_id = "prepended-conversation"
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(uuid4(),),
        replay_seed_each_send=False,
    )

    with pytest.raises(Exception, match="Error normalizing prompt with conversation ID"):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"),
            target=prompt_target,
            conversation_id=conversation_id,
            send_context=target_context,
        )

    assert target_context.target_invocation_count == 0
    mock_memory_instance.add_message_to_memory_async.assert_not_called()


async def test_child_task_target_failure_is_persisted(mock_memory_instance):
    class ChildTaskTarget(MockPromptTarget):
        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            async def fail_in_child_task_async() -> list[Message]:
                raise RuntimeError("provider failed")

            return await asyncio.create_task(fail_in_child_task_async())

    conversation_id = "prepended-conversation"
    seed = Message.from_prompt(prompt="seed", role="user")
    seed.get_piece().conversation_id = conversation_id
    mock_memory_instance.get_conversation_messages_async = AsyncMock(return_value=[seed])
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )

    with pytest.raises(Exception, match="Error sending prompt with conversation ID"):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"),
            target=ChildTaskTarget(),
            conversation_id=conversation_id,
            send_context=target_context,
        )

    assert target_context.target_invocation_count == 1
    assert not target_context.is_seed_consumed
    assert mock_memory_instance.add_message_to_memory_async.call_count == 2
    persisted_values = [
        call.kwargs["request"].get_value() for call in mock_memory_instance.add_message_to_memory_async.call_args_list
    ]
    assert "request" in persisted_values


async def test_concurrent_rejection_is_not_misclassified_as_target_invocation(mock_memory_instance):
    conversation_id = "prepended-conversation"
    seed = Message.from_prompt(prompt="seed", role="user")
    seed.get_piece().conversation_id = conversation_id
    mock_memory_instance.get_conversation_messages_async = AsyncMock(return_value=[seed])
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    target_started = asyncio.Event()
    target_release = asyncio.Event()

    async def wait_in_target(*, normalized_conversation: list[Message]) -> list[Message]:
        target_started.set()
        await target_release.wait()
        raise RuntimeError("provider failed")

    target._send_prompt_to_target_async = AsyncMock(side_effect=wait_in_target)  # type: ignore[method-assign]
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )
    normalizer = PromptNormalizer()
    first_send = asyncio.create_task(
        normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="first request", role="user"),
            target=target,
            conversation_id=conversation_id,
            send_context=target_context,
        )
    )
    await target_started.wait()

    with pytest.raises(Exception, match="Error normalizing prompt"):
        await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="concurrent request", role="user"),
            target=target,
            conversation_id=conversation_id,
            send_context=target_context,
        )

    mock_memory_instance.add_message_to_memory_async.assert_not_called()
    target_release.set()
    with pytest.raises(Exception, match="Error sending prompt"):
        await first_send
    persisted_values = [
        call.kwargs["request"].get_value() for call in mock_memory_instance.add_message_to_memory_async.call_args_list
    ]
    assert "concurrent request" not in persisted_values


async def test_send_prompt_async_empty_response_exception_is_persisted(mock_memory_instance):
    prompt_target = MagicMock(spec=PromptTarget)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(side_effect=EmptyResponseException(message="normalization failed"))
    conversation_id = "prepended-conversation"
    response = await PromptNormalizer().send_prompt_async(
        message=Message.from_prompt(prompt="request", role="user"),
        target=prompt_target,
        conversation_id=conversation_id,
    )

    assert response.get_piece().response_error == "empty"
    assert mock_memory_instance.add_message_to_memory_async.call_count == 2


async def test_send_prompt_async_no_response_adds_memory(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=None)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert mock_memory_instance.add_message_to_memory_async.call_count == 2

    request = mock_memory_instance.add_message_to_memory_async.call_args[1]["request"]
    assert_message_piece_hashes_set(request)
    assert response.message_pieces[0].response_error == "empty"
    assert response.message_pieces[0].original_value == ""
    assert response.message_pieces[0].original_value_data_type == "text"
    assert_message_piece_hashes_set(response)


async def test_send_prompt_async_empty_response_exception_handled(mock_memory_instance, seed_group):
    # Use MagicMock with send_prompt_async as AsyncMock to avoid coroutine warnings on other methods
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=EmptyResponseException(message="Empty response"))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory_async.call_count == 2

    assert response.message_pieces[0].response_error == "empty"
    assert response.message_pieces[0].original_value == ""
    assert response.message_pieces[0].original_value_data_type == "text"

    assert_message_piece_hashes_set(response)


async def test_send_prompt_async_request_response_added_to_memory(mock_memory_instance, seed_group):
    # Use MagicMock with send_prompt_async as AsyncMock to avoid coroutine warnings
    prompt_target = MagicMock()
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    response = MessagePiece(role="assistant", original_value="test_response").to_message()

    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory_async.call_count == 2

    seed_prompt_value = seed_group.prompts[0].value
    # Validate that first request is added to memory, then response is added to memory
    assert (
        seed_prompt_value
        == mock_memory_instance.add_message_to_memory_async.call_args_list[0][1]["request"]
        .message_pieces[0]
        .original_value
    )
    assert (
        mock_memory_instance.add_message_to_memory_async.call_args_list[1][1]["request"]
        .message_pieces[0]
        .original_value
        == "test_response"
    )

    assert mock_memory_instance.add_message_to_memory_async.call_args_list[1].called_after(
        prompt_target.send_prompt_async
    )


async def test_send_prompt_async_exception(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=ValueError("test_exception"))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    seed_prompt_value = seed_group.prompts[0].value

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_prompt_value, role="user")

    with pytest.raises(Exception, match="Error sending prompt with conversation ID"):
        await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory_async.call_count == 2

    # Validate that first request is added to memory, then exception is added to memory
    assert (
        seed_prompt_value
        == mock_memory_instance.add_message_to_memory_async.call_args_list[0][1]["request"]
        .message_pieces[0]
        .original_value
    )
    assert (
        "test_exception"
        in mock_memory_instance.add_message_to_memory_async.call_args_list[1][1]["request"]
        .message_pieces[0]
        .original_value
    )


async def test_send_prompt_async_empty_exception(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=Exception(""))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    with pytest.raises(Exception, match="Error sending prompt with conversation ID"):
        await normalizer.send_prompt_async(message=message, target=prompt_target)


async def test_send_prompt_async_different_sequences(mock_memory_instance):
    """Test that sending messages with different sequences raises ValueError."""
    conv_id = str(uuid4())
    piece1 = MessagePiece(role="user", original_value="test1", sequence=1, conversation_id=conv_id)
    piece2 = MessagePiece(role="user", original_value="test2", sequence=2, conversation_id=conv_id)

    with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry"):
        Message(message_pieces=[piece1, piece2])


async def test_send_prompt_async_mixed_sequence_types(mock_memory_instance):
    """Test that sending messages with mixed sequence types (None and int) raises ValueError."""
    conv_id = str(uuid4())
    piece1 = MessagePiece(role="user", original_value="test1", sequence=1, conversation_id=conv_id)
    piece2 = MessagePiece(role="user", original_value="test2", sequence=1, conversation_id=conv_id)
    # Manually set different sequence to test validation
    piece2.sequence = None  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry"):
        Message(message_pieces=[piece1, piece2])


async def test_send_prompt_async_adds_memory_twice(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert mock_memory_instance.add_message_to_memory_async.call_count == 2


async def test_send_prompt_async_no_converters_response(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    # Send prompt async and check the response
    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert response.get_value() == "Hello", "There were no response converters"


async def test_send_prompt_async_converters_response(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    response_converter = ConverterConfiguration(converters=[Base64Converter()], indexes_to_apply=[0])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(
        message=message,
        response_converter_configurations=[response_converter],
        target=prompt_target,
    )

    assert response.get_value() == "SGVsbG8="


async def test_send_prompt_async_image_converter(mock_memory_instance):
    prompt_target = MagicMock(PromptTarget)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(
        return_value=[MessagePiece(role="assistant", original_value="response").to_message()]
    )

    mock_image_converter = MagicMock(Converter)

    filename = ""

    with tempfile.NamedTemporaryFile(delete=False) as f:
        filename = f.name
        f.write(b"Hello")

        mock_image_converter.convert_tokens_async = AsyncMock(
            return_value=ConverterResult(
                output_type="image_path",
                output_text=filename,
            )
        )

        converters = ConverterConfiguration(converters=[mock_image_converter])

        prompt_text = "Hello"

        seed_group = SeedGroup(seeds=[SeedPrompt(value=prompt_text, data_type="text")])

        normalizer = PromptNormalizer()
        # Mock the async read_file method
        normalizer._memory.results_storage_io.read_file_async = AsyncMock(return_value=b"mocked data")

        message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
        response = await normalizer.send_prompt_async(
            message=message,
            target=prompt_target,
            request_converter_configurations=[converters],
        )

        # verify the prompt target received the correct arguments from the normalizer
        sent_request = prompt_target.send_prompt_async.call_args.kwargs["message"].message_pieces[0]
        assert sent_request.converted_value == filename
        assert sent_request.converted_value_data_type == "image_path"

        assert_message_piece_hashes_set(response)
    os.remove(filename)


@pytest.mark.parametrize("max_requests_per_minute", [None, 10])
@pytest.mark.parametrize("batch_size", [1, 10])
async def test_prompt_normalizer_send_prompt_batch_async_throws(
    mock_memory_instance, seed_group, max_requests_per_minute, batch_size
):
    prompt_target = MockPromptTarget(rpm=max_requests_per_minute)

    request_converters = ConverterConfiguration(converters=[Base64Converter(), StringJoinConverter(join_value="_")])

    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
    normalizer_request = NormalizerRequest(
        message=message,
        request_converter_configurations=[request_converters],
    )

    normalizer = PromptNormalizer()

    # Mock asyncio.sleep to avoid 6s delay in rate limiting test
    with patch("asyncio.sleep", new_callable=AsyncMock):
        if max_requests_per_minute and batch_size != 1:
            with pytest.raises(ValueError):
                results = await normalizer.send_prompt_batch_to_target_async(
                    requests=[normalizer_request],
                    target=prompt_target,
                    batch_size=batch_size,
                )
        else:
            results = await normalizer.send_prompt_batch_to_target_async(
                requests=[normalizer_request],
                target=prompt_target,
                batch_size=batch_size,
            )

            assert "S_G_V_s_b_G_8_=" in prompt_target.prompt_sent
            assert len(results) == 1


async def test_prompt_normalizer_send_prompt_batch_async_applies_labels(mock_memory_instance, seed_group):
    prompt_target = MockPromptTarget()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
    normalizer_request = NormalizerRequest(message=message)

    normalizer = PromptNormalizer()
    results = await normalizer.send_prompt_batch_to_target_async(
        requests=[normalizer_request],
        target=prompt_target,
        batch_size=1,
    )

    assert len(results) == 1


async def test_prompt_normalizer_send_prompt_batch_async_rejects_negative_batch_size(mock_memory_instance, seed_group):
    prompt_target = MockPromptTarget()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
    normalizer_request = NormalizerRequest(message=message)
    normalizer = PromptNormalizer()

    with pytest.raises(ValueError, match="batch_size must be an integer of at least 1"):
        await normalizer.send_prompt_batch_to_target_async(
            requests=[normalizer_request],
            target=prompt_target,
            batch_size=-1,
        )

    assert prompt_target.prompt_sent == []


async def test_prompt_normalizer_send_prompt_batch_async_preserves_empty_response_alignment(
    mock_memory_instance,
):
    prompt_target = MagicMock()
    prompt_target._max_requests_per_minute = None
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    prompt_target.send_prompt_async = AsyncMock(
        side_effect=[
            [MessagePiece(role="assistant", original_value="response 1", conversation_id="conv-1").to_message()],
            None,
        ]
    )

    normalizer = PromptNormalizer()
    requests = [
        NormalizerRequest(
            message=Message.from_prompt(prompt="prompt 1", role="user"),
            conversation_id="conv-1",
        ),
        NormalizerRequest(
            message=Message.from_prompt(prompt="prompt 2", role="user"),
            conversation_id="conv-2",
        ),
    ]

    results = await normalizer.send_prompt_batch_to_target_async(requests=requests, target=prompt_target, batch_size=2)

    assert len(results) == 2
    assert results[0].message_pieces[0].original_value == "response 1"
    assert results[1].message_pieces[0].response_error == "empty"
    assert results[1].message_pieces[0].original_value == ""
    assert results[1].message_pieces[0].conversation_id == "conv-2"


async def test_send_prompt_async_none_in_list_response_returns_empty(mock_memory_instance, seed_group):
    """Target returning [None] (list containing None) should produce an empty response."""
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=[None])
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert response.message_pieces[0].response_error == "empty"
    assert response.message_pieces[0].original_value == ""


async def test_build_message(mock_memory_instance, seed_group):
    # This test is obsolete since _build_message was removed and message preparation
    # is now done inline in send_prompt_async. The functionality is tested by
    # other send_prompt_async tests that verify message pieces have correct
    # conversation_id, sequence, and role values.
    pass


async def test_convert_response_values_index(mock_memory_instance, response: Message):
    response_converter = ConverterConfiguration(converters=[Base64Converter()], indexes_to_apply=[0])

    normalizer = PromptNormalizer()

    await normalizer.convert_values_async(converter_configurations=[response_converter], message=response)
    assert response.get_value() == "SGVsbG8=", "Converter should be applied here"
    assert response.get_value(1) == "part 2", "Converter should not be applied since we specified only 0"


async def test_convert_response_values_type(mock_memory_instance, response: Message):
    response_converter = ConverterConfiguration(converters=[Base64Converter()], prompt_data_types_to_apply=["text"])

    normalizer = PromptNormalizer()

    await normalizer.convert_values_async(converter_configurations=[response_converter], message=response)
    assert response.get_value() == "SGVsbG8="
    assert response.get_value(1) == "cGFydCAy"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("stage", ["request", "response"])
async def test_converter_guard_covers_only_selected_conversions_async(
    *, mock_memory_instance: MagicMock, stage: str
) -> None:
    converter, skipped = Base64Converter(), Base64Converter()
    guarded: list[Converter] = []
    active: set[Converter] = set()
    target = MockPromptTarget()
    convert = converter.convert_async
    send = target._send_prompt_to_target_async

    @asynccontextmanager
    async def guard_async(value: Converter) -> AsyncGenerator[None, None]:
        guarded.append(value)
        active.add(value)
        try:
            yield
        finally:
            active.remove(value)

    async def convert_async(*, prompt: str, input_type: PromptDataType) -> ConverterResult:
        assert active == {converter}
        return await convert(prompt=prompt, input_type=input_type)

    async def send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        assert not active
        return await send(normalized_conversation=normalized_conversation)

    configurations = [
        ConverterConfiguration(converters=[skipped], indexes_to_apply=[9]),
        ConverterConfiguration(converters=[skipped], prompt_data_types_to_apply=["image_path"]),
        ConverterConfiguration(converters=[converter]),
    ]
    with (
        patch.object(converter, "convert_async", side_effect=convert_async),
        patch.object(target, "_send_prompt_to_target_async", side_effect=send_async),
    ):
        await PromptNormalizer(converter_guard=guard_async).send_prompt_async(
            message=Message.from_prompt(prompt="Hello", role="user"),
            target=target,
            request_converter_configurations=configurations if stage == "request" else None,
            response_converter_configurations=configurations if stage == "response" else None,
        )
    assert guarded == [converter]
    assert not active


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("error", [RuntimeError("conversion failed"), asyncio.CancelledError()])
async def test_converter_guard_releases_on_failure_async(
    *, mock_memory_instance: MagicMock, error: BaseException
) -> None:
    active = False
    converter = Base64Converter()

    @asynccontextmanager
    async def guard_async(value: Converter) -> AsyncGenerator[None, None]:
        nonlocal active
        assert value is converter
        active = True
        try:
            yield
        finally:
            active = False

    with patch.object(converter, "convert_async", side_effect=error), pytest.raises(type(error)):
        await PromptNormalizer(converter_guard=guard_async).convert_values_async(
            converter_configurations=[ConverterConfiguration(converters=[converter])],
            message=Message.from_prompt(prompt="Hello", role="user"),
        )
    assert not active


async def test_send_prompt_async_exception_conv_id(mock_memory_instance, seed_group):
    prompt_target = MagicMock(PromptTarget)
    prompt_target.send_prompt_async = AsyncMock(side_effect=Exception("Test Exception"))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    with pytest.raises(Exception, match="Error sending prompt with conversation ID: 123"):
        await normalizer.send_prompt_async(message=message, target=prompt_target, conversation_id="123")

    # Validate that first request is added to memory, then exception is added to memory
    assert (
        seed_group.prompts[0].value
        == mock_memory_instance.add_message_to_memory_async.call_args_list[0][1]["request"]
        .message_pieces[0]
        .original_value
    )
    assert (
        "Test Exception"
        in mock_memory_instance.add_message_to_memory_async.call_args_list[1][1]["request"]
        .message_pieces[0]
        .original_value
    )


# Tests for execution context in converter operations (used for error message handling)


class ContextCapturingConverter(Converter):
    """A converter that captures the execution context during conversion."""

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    captured_context = None

    def __init__(self) -> None:
        pass

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        # Capture the current execution context
        ContextCapturingConverter.captured_context = get_execution_context()
        return ConverterResult(output_text=f"converted:{prompt}", output_type="text")

    def input_supported(self, input_type: PromptDataType) -> bool:
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        return output_type == "text"


class FailingConverter(Converter):
    """A converter that raises an exception during conversion."""

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    def __init__(self) -> None:
        pass

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        raise RuntimeError("Converter failed")

    def input_supported(self, input_type: PromptDataType) -> bool:
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        return output_type == "text"


class TestPromptNormalizerConverterContext:
    """Tests for execution context during converter operations in PromptNormalizer."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()
        ContextCapturingConverter.captured_context = None

    async def test_convert_values_sets_converter_context(self, mock_memory_instance):
        """Test that convert_values_async sets CONVERTER execution context."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = ConverterConfiguration(converters=[ContextCapturingConverter()])

        await normalizer.convert_values_async(converter_configurations=[converter_config], message=message)

        # The converter should have captured the execution context
        captured = ContextCapturingConverter.captured_context
        assert captured is not None
        assert captured.component_role == ComponentRole.CONVERTER

    async def test_convert_values_inherits_outer_context(self, mock_memory_instance):
        """Test that converter context inherits attack info from outer context."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = ConverterConfiguration(converters=[ContextCapturingConverter()])

        # Set an outer execution context (simulating being called from an attack)
        with execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="TestAttack",
            attack_identifier=get_mock_attack_identifier("TestAttack"),
            objective_target_conversation_id="conv-456",
        ):
            await normalizer.convert_values_async(converter_configurations=[converter_config], message=message)

        # The converter should have captured the context with inherited values
        captured = ContextCapturingConverter.captured_context
        assert captured is not None
        assert captured.component_role == ComponentRole.CONVERTER
        assert captured.attack_strategy_name == "TestAttack"
        assert captured.objective_target_conversation_id == "conv-456"

    async def test_convert_values_exception_propagates(self, mock_memory_instance):
        """Test that converter exceptions propagate correctly."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = ConverterConfiguration(converters=[FailingConverter()])

        with pytest.raises(RuntimeError, match="Converter failed"):
            await normalizer.convert_values_async(converter_configurations=[converter_config], message=message)

    async def test_convert_values_context_includes_converter_identifier(self, mock_memory_instance):
        """Test that converter context includes the converter's identifier."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter = ContextCapturingConverter()
        converter_config = ConverterConfiguration(converters=[converter])

        await normalizer.convert_values_async(converter_configurations=[converter_config], message=message)

        captured = ContextCapturingConverter.captured_context
        assert captured is not None
        assert captured.component_identifier is not None
        assert "ContextCapturingConverter" in str(captured.component_identifier)


def test_memory_property_raises_when_memory_none():
    """Guard at line 45: _memory is None raises RuntimeError."""
    normalizer = PromptNormalizer.__new__(PromptNormalizer)
    normalizer._memory = None
    with pytest.raises(RuntimeError, match="Memory is not initialized"):
        _ = normalizer.memory


async def test_add_prepended_conversation_to_memory(mock_memory_instance):
    normalizer = PromptNormalizer()
    conv_id = "test-conv-id"

    piece = MessagePiece(role="user", original_value="prepended text", conversation_id="old-id")
    message = Message(message_pieces=[piece])

    result = await normalizer.add_prepended_conversation_to_memory_async(
        conversation_id=conv_id,
        should_convert=False,
        prepended_conversation=[message],
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].message_pieces[0].conversation_id == conv_id
    mock_memory_instance.add_message_to_memory_async.assert_called_once()


_AUDIO_SAMPLE_RATE_HZ = 24000
_AUDIO_NUM_CHANNELS = 1
_AUDIO_SAMPLE_WIDTH_BYTES = 2


def _write_test_wav(*, pcm: bytes, sample_rate_hz: int, num_channels: int, sample_width_bytes: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wav_out:
        wav_out.setnchannels(num_channels)
        wav_out.setsampwidth(sample_width_bytes)
        wav_out.setframerate(sample_rate_hz)
        wav_out.writeframes(pcm)
    return path


@pytest.fixture
def sample_pcm() -> bytes:
    return b"\x01\x02\x03\x04" * 1000


@pytest.fixture
def dummy_audio_converter_config() -> ConverterConfiguration:
    return ConverterConfiguration(converters=[MagicMock(spec=Converter)])


async def test_convert_audio_async_no_converters_returns_input_unchanged(mock_memory_instance, sample_pcm):
    normalizer = PromptNormalizer()
    with patch.object(normalizer, "convert_values_async", new_callable=AsyncMock) as mock_convert:
        result = await normalizer.convert_audio_async(
            raw_pcm=sample_pcm,
            converter_configurations=[],
            sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
            num_channels=_AUDIO_NUM_CHANNELS,
            sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
        )
    assert result == sample_pcm
    mock_convert.assert_not_called()


async def test_convert_audio_async_no_op_converter_round_trips_pcm(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    normalizer = PromptNormalizer()
    with patch.object(normalizer, "convert_values_async", new_callable=AsyncMock):
        result = await normalizer.convert_audio_async(
            raw_pcm=sample_pcm,
            converter_configurations=[dummy_audio_converter_config],
            sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
            num_channels=_AUDIO_NUM_CHANNELS,
            sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
        )
    assert result == sample_pcm


async def test_convert_audio_async_returns_pcm_from_converted_value(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    transformed_pcm = b"\xfb\xfc\xfd\xfe" * 1000
    new_wav_path = _write_test_wav(
        pcm=transformed_pcm,
        sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
        num_channels=_AUDIO_NUM_CHANNELS,
        sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
    )

    async def swap_converted_value(*, converter_configurations, message):
        message.message_pieces[0].converted_value = new_wav_path

    normalizer = PromptNormalizer()
    try:
        with patch.object(normalizer, "convert_values_async", side_effect=swap_converted_value):
            result = await normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        assert result == transformed_pcm
    finally:
        Path(new_wav_path).unlink(missing_ok=True)


async def test_convert_audio_async_cleans_up_temp_file_on_success(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    captured_paths: list[str] = []

    async def capture_input_path(*, converter_configurations, message):
        captured_paths.append(message.message_pieces[0].converted_value)

    normalizer = PromptNormalizer()
    with patch.object(normalizer, "convert_values_async", side_effect=capture_input_path):
        await normalizer.convert_audio_async(
            raw_pcm=sample_pcm,
            converter_configurations=[dummy_audio_converter_config],
            sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
            num_channels=_AUDIO_NUM_CHANNELS,
            sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
        )
    assert len(captured_paths) == 1
    assert not Path(captured_paths[0]).exists()


async def test_convert_audio_async_cleans_up_temp_file_on_converter_failure(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    captured_paths: list[str] = []

    async def capture_then_raise(*, converter_configurations, message):
        captured_paths.append(message.message_pieces[0].converted_value)
        raise RuntimeError("converter blew up")

    normalizer = PromptNormalizer()
    with patch.object(normalizer, "convert_values_async", side_effect=capture_then_raise):
        with pytest.raises(RuntimeError, match="converter blew up"):
            await normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
    assert len(captured_paths) == 1
    assert not Path(captured_paths[0]).exists()


async def test_convert_audio_async_raises_on_sample_rate_mismatch(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    wrong_rate_path = _write_test_wav(
        pcm=b"\x00" * 100,
        sample_rate_hz=16000,
        num_channels=_AUDIO_NUM_CHANNELS,
        sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
    )

    async def swap_to_wrong_rate(*, converter_configurations, message):
        message.message_pieces[0].converted_value = wrong_rate_path

    normalizer = PromptNormalizer()
    try:
        with patch.object(normalizer, "convert_values_async", side_effect=swap_to_wrong_rate):
            with pytest.raises(ValueError, match="format mismatch"):
                await normalizer.convert_audio_async(
                    raw_pcm=sample_pcm,
                    converter_configurations=[dummy_audio_converter_config],
                    sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                    num_channels=_AUDIO_NUM_CHANNELS,
                    sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
                )
    finally:
        Path(wrong_rate_path).unlink(missing_ok=True)


async def test_convert_audio_async_raises_on_channel_mismatch(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    stereo_pcm = b"\x00\x01\x02\x03" * 100
    wrong_channels_path = _write_test_wav(
        pcm=stereo_pcm,
        sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
        num_channels=2,
        sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
    )

    async def swap_to_stereo(*, converter_configurations, message):
        message.message_pieces[0].converted_value = wrong_channels_path

    normalizer = PromptNormalizer()
    try:
        with patch.object(normalizer, "convert_values_async", side_effect=swap_to_stereo):
            with pytest.raises(ValueError, match="format mismatch"):
                await normalizer.convert_audio_async(
                    raw_pcm=sample_pcm,
                    converter_configurations=[dummy_audio_converter_config],
                    sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                    num_channels=_AUDIO_NUM_CHANNELS,
                    sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
                )
    finally:
        Path(wrong_channels_path).unlink(missing_ok=True)


async def test_convert_audio_async_runs_file_work_off_the_event_loop(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    event_loop_thread = threading.get_ident()
    file_work_threads: dict[str, int] = {}

    def record_thread(name, function):
        def run(*args, **kwargs):
            file_work_threads[name] = threading.get_ident()
            return function(*args, **kwargs)

        return run

    normalizer = PromptNormalizer()
    with (
        patch.object(
            prompt_normalizer_module,
            "_write_pcm_to_temp_wav",
            side_effect=record_thread("write", prompt_normalizer_module._write_pcm_to_temp_wav),
        ),
        patch.object(
            prompt_normalizer_module,
            "_read_pcm_from_wav",
            side_effect=record_thread("read", prompt_normalizer_module._read_pcm_from_wav),
        ),
        patch.object(Path, "unlink", autospec=True, side_effect=record_thread("unlink", Path.unlink)),
        patch.object(normalizer, "convert_values_async", new_callable=AsyncMock),
    ):
        result = await normalizer.convert_audio_async(
            raw_pcm=sample_pcm,
            converter_configurations=[dummy_audio_converter_config],
            sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
            num_channels=_AUDIO_NUM_CHANNELS,
            sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
        )

    assert result == sample_pcm
    assert set(file_work_threads) == {"write", "read", "unlink"}
    assert event_loop_thread not in file_work_threads.values()


async def test_convert_audio_async_cleans_up_temp_file_when_cancelled_during_conversion(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    captured_paths: list[str] = []
    converting = asyncio.Event()

    async def capture_then_wait(*, converter_configurations, message):
        captured_paths.append(message.message_pieces[0].converted_value)
        converting.set()
        await asyncio.Event().wait()

    normalizer = PromptNormalizer()
    with patch.object(normalizer, "convert_values_async", side_effect=capture_then_wait):
        task = asyncio.create_task(
            normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        )
        await converting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(captured_paths) == 1
    assert not Path(captured_paths[0]).exists()


async def test_convert_audio_async_removes_temp_file_written_after_cancellation(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    write_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    release_write = threading.Event()
    release_cleanup = threading.Event()
    written_paths: list[str] = []
    cleanup_threads: list[int] = []
    write_pcm_to_temp_wav = prompt_normalizer_module._write_pcm_to_temp_wav
    unlink = Path.unlink

    def write_after_release(**kwargs):
        loop.call_soon_threadsafe(write_started.set)
        if not release_write.wait(TEST_WATCHDOG_SECONDS):
            raise TimeoutError("The test did not release the WAV writer.")
        written_paths.append(write_pcm_to_temp_wav(**kwargs))
        return written_paths[-1]

    def hold_cleanup(path, *, missing_ok=False):
        cleanup_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(cleanup_started.set)
        try:
            if cleanup_threads[-1] != event_loop_thread and not release_cleanup.wait(TEST_WATCHDOG_SECONDS):
                raise TimeoutError("The test did not release WAV cleanup.")
            return unlink(path, missing_ok=missing_ok)
        finally:
            loop.call_soon_threadsafe(cleanup_finished.set)

    normalizer = PromptNormalizer()
    with (
        patch.object(prompt_normalizer_module, "_write_pcm_to_temp_wav", side_effect=write_after_release),
        patch.object(Path, "unlink", autospec=True, side_effect=hold_cleanup),
        patch.object(normalizer, "convert_values_async", new_callable=AsyncMock) as mock_convert,
    ):
        task = asyncio.create_task(
            normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        )
        try:
            await wait_for_worker_ready_async(started=write_started, operation=task)
            assert not written_paths
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait_for_completion_async(future=task)
            assert not written_paths

            release_write.set()
            await wait_for_event_async(event=cleanup_started, tasks=[])
            assert cleanup_threads[0] != event_loop_thread
            heartbeat = asyncio.create_task(asyncio.sleep(0))
            await wait_for_completion_async(future=heartbeat)
            assert not cleanup_finished.is_set()
        finally:
            release_write.set()
            release_cleanup.set()
            if not task.done():
                task.cancel()
            await drain_tasks_async(task)
            if write_started.is_set():
                await wait_for_event_async(event=cleanup_finished, tasks=[])

    assert len(written_paths) == 1
    assert not Path(written_paths[0]).exists()
    mock_convert.assert_not_called()


@pytest.mark.parametrize("reader_fails", [False, True])
async def test_convert_audio_async_waits_for_cancelled_reader_before_removing_input(
    mock_memory_instance, sample_pcm, reader_fails
):
    loop = asyncio.get_running_loop()
    read_started = asyncio.Event()
    read_closed = asyncio.Event()
    release_read = threading.Event()
    read_finished = threading.Event()
    paths: list[str] = []
    cleanup_after_read: list[bool] = []
    cleanup_scheduled_early: list[bool] = []
    unlink = Path.unlink
    to_thread = asyncio.to_thread

    def held_read(path):
        paths.append(path)
        try:
            with wave.open(path, "rb") as wav_in:
                loop.call_soon_threadsafe(read_started.set)
                if not release_read.wait(TEST_WATCHDOG_SECONDS):
                    raise TimeoutError("The test did not release the WAV reader.")
                if reader_fails:
                    raise OSError("WAV read failed after cancellation.")
                return (
                    wav_in.getframerate(),
                    wav_in.getnchannels(),
                    wav_in.getsampwidth(),
                    wav_in.readframes(wav_in.getnframes()),
                )
        finally:
            read_finished.set()
            loop.call_soon_threadsafe(read_closed.set)

    def record_unlink(path, *, missing_ok=False):
        cleanup_after_read.append(read_finished.is_set())
        return unlink(path, missing_ok=missing_ok)

    async def observe_to_thread_async(function, *args, **kwargs):
        if getattr(function, "__name__", "") == "unlink":
            cleanup_scheduled_early.append(not read_finished.is_set())
        return await to_thread(function, *args, **kwargs)

    normalizer = PromptNormalizer()
    config = ConverterConfiguration(
        converters=[MagicMock(spec=Converter)],
        prompt_data_types_to_apply=["text"],
    )
    with (
        patch.object(prompt_normalizer_module, "_read_pcm_from_wav", side_effect=held_read),
        patch.object(Path, "unlink", autospec=True, side_effect=record_unlink),
        patch.object(asyncio, "to_thread", side_effect=observe_to_thread_async),
    ):
        existing_tasks = asyncio.all_tasks()
        task = asyncio.create_task(
            normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        )
        try:
            await wait_for_worker_ready_async(started=read_started, operation=task)
            task.cancel()
            # Loop shutdown also cancels Tasks created by the operation.
            owned_tasks = asyncio.all_tasks() - existing_tasks - {task}
            for owned_task in owned_tasks:
                owned_task.cancel()
            await drain_tasks_async(*owned_tasks)
            await asyncio.sleep(0)
            assert not task.done()
            assert not cleanup_scheduled_early
            assert not cleanup_after_read

            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release_read.set()
            with pytest.raises(asyncio.CancelledError) as cancellation:
                await wait_for_completion_async(future=task)
            if reader_fails:
                assert isinstance(cancellation.value.__cause__, OSError)
            assert cleanup_after_read == [True]
            assert paths and not Path(paths[0]).exists()
        finally:
            release_read.set()
            if not task.done():
                task.cancel()
            await drain_tasks_async(task)
            if read_started.is_set():
                await wait_for_event_async(event=read_closed, tasks=[])
            for path in paths:
                unlink(Path(path), missing_ok=True)


async def test_convert_audio_async_removes_completed_write_cancelled_before_handoff(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    cleaned = asyncio.Event()
    cancellation_requested = threading.Event()
    paths: list[str] = []
    cleanup_threads: list[int] = []
    record_path = prompt_normalizer_module._TempWavWrite.record_path
    unlink = Path.unlink

    def cancel_before_handoff() -> None:
        task.cancel()
        cancellation_requested.set()

    def record_then_cancel(ownership, path):
        accepted = record_path(ownership, path)
        paths.append(path)
        loop.call_soon_threadsafe(cancel_before_handoff)
        assert cancellation_requested.wait(TEST_WATCHDOG_SECONDS)
        return accepted

    def record_unlink(path, *, missing_ok=False):
        cleanup_threads.append(threading.get_ident())
        try:
            return unlink(path, missing_ok=missing_ok)
        finally:
            loop.call_soon_threadsafe(cleaned.set)

    normalizer = PromptNormalizer()
    with (
        patch.object(
            prompt_normalizer_module._TempWavWrite, "record_path", autospec=True, side_effect=record_then_cancel
        ),
        patch.object(Path, "unlink", autospec=True, side_effect=record_unlink),
        patch.object(normalizer, "convert_values_async", new_callable=AsyncMock) as mock_convert,
    ):
        task = asyncio.create_task(
            normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                await wait_for_completion_async(future=task)
            await wait_for_event_async(event=cleaned, tasks=[])
            assert cancellation_requested.is_set()
            assert len(paths) == 1
            assert cleanup_threads and cleanup_threads[0] != event_loop_thread
            assert not Path(paths[0]).exists()
        finally:
            cancellation_requested.set()
            if not task.done():
                task.cancel()
            await drain_tasks_async(task)
            if paths:
                await wait_for_event_async(event=cleaned, tasks=[])
                unlink(Path(paths[0]), missing_ok=True)

    mock_convert.assert_not_called()


async def test_convert_audio_async_removes_abandoned_write_during_loop_shutdown(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config
):
    outer_loop = asyncio.get_running_loop()
    shutdown_started = asyncio.Event()
    release_write = threading.Event()
    written_paths: list[str] = []
    write_pcm_to_temp_wav = prompt_normalizer_module._write_pcm_to_temp_wav
    normalizer = PromptNormalizer()

    def run_cancelled_conversion():
        runner = asyncio.Runner()
        loop = runner.get_loop()
        shutdown_executor = loop.shutdown_default_executor

        async def signal_shutdown_async(*args, **kwargs):
            outer_loop.call_soon_threadsafe(shutdown_started.set)
            await shutdown_executor(*args, **kwargs)

        async def convert_and_stop_async():
            write_started = asyncio.Event()

            def write_after_release(**kwargs):
                loop.call_soon_threadsafe(write_started.set)
                if not release_write.wait(TEST_WATCHDOG_SECONDS):
                    raise TimeoutError("The test did not release the WAV writer.")
                path = write_pcm_to_temp_wav(**kwargs)
                written_paths.append(path)
                return path

            with (
                patch.object(prompt_normalizer_module, "_write_pcm_to_temp_wav", side_effect=write_after_release),
                patch.object(normalizer, "convert_values_async", new_callable=AsyncMock),
            ):
                task = asyncio.create_task(
                    normalizer.convert_audio_async(
                        raw_pcm=sample_pcm,
                        converter_configurations=[dummy_audio_converter_config],
                        sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                        num_channels=_AUDIO_NUM_CHANNELS,
                        sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
                    )
                )
                await wait_for_worker_ready_async(started=write_started, operation=task)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await wait_for_completion_async(future=task)

        with patch.object(loop, "shutdown_default_executor", side_effect=signal_shutdown_async), runner:
            runner.run(convert_and_stop_async())

    runner_task = asyncio.create_task(asyncio.to_thread(run_cancelled_conversion))
    try:
        await wait_for_event_async(event=shutdown_started, tasks=[runner_task])
        assert not written_paths
        release_write.set()
        await wait_for_completion_async(future=runner_task)
        assert len(written_paths) == 1
        assert not Path(written_paths[0]).exists()
    finally:
        release_write.set()
        await drain_tasks_async(runner_task)
        for path in written_paths:
            Path(path).unlink(missing_ok=True)


async def test_convert_audio_async_logs_abandoned_cleanup_failure(
    mock_memory_instance, sample_pcm, dummy_audio_converter_config, caplog
):
    loop = asyncio.get_running_loop()
    write_started = asyncio.Event()
    failure_reported = asyncio.Event()
    release_write = threading.Event()
    written_paths: list[str] = []
    write_pcm_to_temp_wav = prompt_normalizer_module._write_pcm_to_temp_wav
    report_failure = prompt_normalizer_module._report_abandoned_temp_wav_failure
    unlink = Path.unlink

    def write_after_release(**kwargs):
        loop.call_soon_threadsafe(write_started.set)
        if not release_write.wait(TEST_WATCHDOG_SECONDS):
            raise TimeoutError("The test did not release the WAV writer.")
        path = write_pcm_to_temp_wav(**kwargs)
        written_paths.append(path)
        return path

    def fail_cleanup(path, *, missing_ok=False):
        raise PermissionError("Test cleanup failure")

    def record_failure(work):
        try:
            report_failure(work)
        finally:
            loop.call_soon_threadsafe(failure_reported.set)

    normalizer = PromptNormalizer()
    caplog.set_level(logging.ERROR, logger=prompt_normalizer_module.__name__)
    with (
        patch.object(prompt_normalizer_module, "_write_pcm_to_temp_wav", side_effect=write_after_release),
        patch.object(prompt_normalizer_module, "_report_abandoned_temp_wav_failure", side_effect=record_failure),
        patch.object(Path, "unlink", autospec=True, side_effect=fail_cleanup),
        patch.object(normalizer, "convert_values_async", new_callable=AsyncMock),
    ):
        task = asyncio.create_task(
            normalizer.convert_audio_async(
                raw_pcm=sample_pcm,
                converter_configurations=[dummy_audio_converter_config],
                sample_rate_hz=_AUDIO_SAMPLE_RATE_HZ,
                num_channels=_AUDIO_NUM_CHANNELS,
                sample_width_bytes=_AUDIO_SAMPLE_WIDTH_BYTES,
            )
        )
        try:
            await wait_for_worker_ready_async(started=write_started, operation=task)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait_for_completion_async(future=task)
            release_write.set()
            await wait_for_event_async(event=failure_reported, tasks=[])
            assert any(record.exc_info and isinstance(record.exc_info[1], PermissionError) for record in caplog.records)
        finally:
            release_write.set()
            if not task.done():
                task.cancel()
            await drain_tasks_async(task)
            if write_started.is_set():
                await wait_for_event_async(event=failure_reported, tasks=[])
            for path in written_paths:
                unlink(Path(path), missing_ok=True)


def _cancelled_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    target.send_prompt_async = AsyncMock(side_effect=asyncio.CancelledError())
    return target


async def test_cancelled_target_call_persists_the_request_without_a_response(mock_memory_instance):
    with pytest.raises(asyncio.CancelledError):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"),
            target=_cancelled_target(),
            conversation_id="cancelled-conversation",
        )

    [persisted] = [call.kwargs["request"] for call in mock_memory_instance.add_message_to_memory_async.await_args_list]
    assert [piece.api_role for piece in persisted.message_pieces] == ["user"]
    assert persisted.get_value() == "request"
    assert persisted.get_piece().conversation_id == "cancelled-conversation"


async def test_cancelled_send_that_never_reached_the_target_persists_nothing(mock_memory_instance):
    conversation_id = "prepended-conversation"
    target_context = PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=(uuid4(),),
        replay_seed_each_send=False,
    )

    with pytest.raises(asyncio.CancelledError):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"),
            target=_cancelled_target(),
            conversation_id=conversation_id,
            send_context=target_context,
        )

    assert target_context.target_invocation_count == 0
    mock_memory_instance.add_message_to_memory_async.assert_not_called()


async def test_failure_to_persist_a_cancelled_request_does_not_mask_the_cancellation(mock_memory_instance):
    mock_memory_instance.add_message_to_memory_async.side_effect = RuntimeError("database unavailable")

    with pytest.raises(asyncio.CancelledError):
        await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt="request", role="user"), target=_cancelled_target()
        )

    mock_memory_instance.add_message_to_memory_async.assert_awaited_once()
