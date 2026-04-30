# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget, get_image_message_piece, get_mock_attack_identifier, get_mock_target_identifier

from pyrit.exceptions import (
    ComponentRole,
    EmptyResponseException,
    clear_execution_context,
    execution_context,
    get_execution_context,
)
from pyrit.memory import CentralMemory
from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedGroup,
    SeedPrompt,
)
from pyrit.prompt_converter import (
    Base64Converter,
    ConverterResult,
    PromptConverter,
    StringJoinConverter,
)
from pyrit.prompt_normalizer import NormalizerRequest, PromptNormalizer
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)
from pyrit.prompt_target import PromptTarget


@pytest.fixture
def response() -> Message:
    conversation_id = "123"
    image_message_piece = get_image_message_piece()
    image_message_piece._role = "assistant"
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
    memory = MagicMock()
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        yield memory


class MockPromptConverter(PromptConverter):
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


def assert_message_piece_hashes_set(request: Message):
    assert request
    assert request.message_pieces
    for piece in request.message_pieces:
        assert piece.original_value_sha256
        assert piece.converted_value_sha256


@pytest.mark.asyncio
async def test_send_prompt_async_multiple_converters(mock_memory_instance, seed_group):
    prompt_target = MockPromptTarget()
    request_converters = [
        PromptConverterConfiguration(converters=[Base64Converter(), StringJoinConverter(join_value="_")])
    ]

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    await normalizer.send_prompt_async(
        message=message, request_converter_configurations=request_converters, target=prompt_target
    )

    assert prompt_target.prompt_sent == ["S_G_V_s_b_G_8_="]


@pytest.mark.asyncio
async def test_send_prompt_async_no_response_adds_memory(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=None)
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert mock_memory_instance.add_message_to_memory.call_count == 2

    request = mock_memory_instance.add_message_to_memory.call_args[1]["request"]
    assert_message_piece_hashes_set(request)
    assert response.message_pieces[0].response_error == "empty"
    assert response.message_pieces[0].original_value == ""
    assert response.message_pieces[0].original_value_data_type == "text"
    assert_message_piece_hashes_set(response)


@pytest.mark.asyncio
async def test_send_prompt_async_labels_emit_deprecation_warning(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(
        return_value=[MessagePiece(role="assistant", original_value="ok", conversation_id="conv-1").to_message()]
    )
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    with patch("pyrit.prompt_normalizer.prompt_normalizer.print_deprecation_message") as mock_deprecation:
        await normalizer.send_prompt_async(message=message, target=prompt_target, labels={"env": "prod"})

    mock_deprecation.assert_called_once()


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_exception_handled(mock_memory_instance, seed_group):
    # Use MagicMock with send_prompt_async as AsyncMock to avoid coroutine warnings on other methods
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=EmptyResponseException(message="Empty response"))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory.call_count == 2

    assert response.message_pieces[0].response_error == "empty"
    assert response.message_pieces[0].original_value == ""
    assert response.message_pieces[0].original_value_data_type == "text"

    assert_message_piece_hashes_set(response)


@pytest.mark.asyncio
async def test_send_prompt_async_request_response_added_to_memory(mock_memory_instance, seed_group):
    # Use MagicMock with send_prompt_async as AsyncMock to avoid coroutine warnings
    prompt_target = MagicMock()

    response = MessagePiece(role="assistant", original_value="test_response").to_message()

    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory.call_count == 2

    seed_prompt_value = seed_group.prompts[0].value
    # Validate that first request is added to memory, then response is added to memory
    assert (
        seed_prompt_value
        == mock_memory_instance.add_message_to_memory.call_args_list[0][1]["request"].message_pieces[0].original_value
    )
    assert (
        mock_memory_instance.add_message_to_memory.call_args_list[1][1]["request"].message_pieces[0].original_value
        == "test_response"
    )

    assert mock_memory_instance.add_message_to_memory.call_args_list[1].called_after(prompt_target.send_prompt_async)


@pytest.mark.asyncio
async def test_send_prompt_async_exception(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=ValueError("test_exception"))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    seed_prompt_value = seed_group.prompts[0].value

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_prompt_value, role="user")

    with pytest.raises(Exception, match="Error sending prompt with conversation ID"):
        await normalizer.send_prompt_async(message=message, target=prompt_target)

    assert mock_memory_instance.add_message_to_memory.call_count == 2

    # Validate that first request is added to memory, then exception is added to memory
    assert (
        seed_prompt_value
        == mock_memory_instance.add_message_to_memory.call_args_list[0][1]["request"].message_pieces[0].original_value
    )
    assert (
        "test_exception"
        in mock_memory_instance.add_message_to_memory.call_args_list[1][1]["request"].message_pieces[0].original_value
    )


@pytest.mark.asyncio
async def test_send_prompt_async_empty_exception(mock_memory_instance, seed_group):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(side_effect=Exception(""))
    prompt_target.get_identifier.return_value = get_mock_target_identifier("MockTarget")

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    with pytest.raises(Exception, match="Error sending prompt with conversation ID"):
        await normalizer.send_prompt_async(message=message, target=prompt_target)


@pytest.mark.asyncio
async def test_send_prompt_async_different_sequences(mock_memory_instance):
    """Test that sending messages with different sequences raises ValueError."""
    conv_id = str(uuid4())
    piece1 = MessagePiece(role="user", original_value="test1", sequence=1, conversation_id=conv_id)
    piece2 = MessagePiece(role="user", original_value="test2", sequence=2, conversation_id=conv_id)

    with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry"):
        Message(message_pieces=[piece1, piece2])


@pytest.mark.asyncio
async def test_send_prompt_async_mixed_sequence_types(mock_memory_instance):
    """Test that sending messages with mixed sequence types (None and int) raises ValueError."""
    conv_id = str(uuid4())
    piece1 = MessagePiece(role="user", original_value="test1", sequence=1, conversation_id=conv_id)
    piece2 = MessagePiece(role="user", original_value="test2", sequence=1, conversation_id=conv_id)
    # Manually set different sequence to test validation
    piece2.sequence = None  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry"):
        Message(message_pieces=[piece1, piece2])


@pytest.mark.asyncio
async def test_send_prompt_async_adds_memory_twice(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert mock_memory_instance.add_message_to_memory.call_count == 2


@pytest.mark.asyncio
async def test_send_prompt_async_no_converters_response(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    # Send prompt async and check the response
    response = await normalizer.send_prompt_async(message=message, target=prompt_target)
    assert response.get_value() == "Hello", "There were no response converters"


@pytest.mark.asyncio
async def test_send_prompt_async_converters_response(mock_memory_instance, seed_group, response: Message):
    prompt_target = MagicMock()
    prompt_target.send_prompt_async = AsyncMock(return_value=[response])

    response_converter = PromptConverterConfiguration(converters=[Base64Converter()], indexes_to_apply=[0])

    normalizer = PromptNormalizer()
    message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")

    response = await normalizer.send_prompt_async(
        message=message,
        response_converter_configurations=[response_converter],
        target=prompt_target,
    )

    assert response.get_value() == "SGVsbG8="


@pytest.mark.asyncio
async def test_send_prompt_async_image_converter(mock_memory_instance):
    prompt_target = MagicMock(PromptTarget)
    prompt_target.send_prompt_async = AsyncMock(
        return_value=[MessagePiece(role="assistant", original_value="response").to_message()]
    )

    mock_image_converter = MagicMock(PromptConverter)

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

        prompt_converters = PromptConverterConfiguration(converters=[mock_image_converter])

        prompt_text = "Hello"

        seed_group = SeedGroup(seeds=[SeedPrompt(value=prompt_text, data_type="text")])

        normalizer = PromptNormalizer()
        # Mock the async read_file method
        normalizer._memory.results_storage_io.read_file = AsyncMock(return_value=b"mocked data")

        message = Message.from_prompt(prompt=seed_group.prompts[0].value, role="user")
        response = await normalizer.send_prompt_async(
            message=message,
            target=prompt_target,
            request_converter_configurations=[prompt_converters],
        )

        # verify the prompt target received the correct arguments from the normalizer
        sent_request = prompt_target.send_prompt_async.call_args.kwargs["message"].message_pieces[0]
        assert sent_request.converted_value == filename
        assert sent_request.converted_value_data_type == "image_path"

        assert_message_piece_hashes_set(response)
    os.remove(filename)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_requests_per_minute", [None, 10])
@pytest.mark.parametrize("batch_size", [1, 10])
async def test_prompt_normalizer_send_prompt_batch_async_throws(
    mock_memory_instance, seed_group, max_requests_per_minute, batch_size
):
    prompt_target = MockPromptTarget(rpm=max_requests_per_minute)

    request_converters = PromptConverterConfiguration(
        converters=[Base64Converter(), StringJoinConverter(join_value="_")]
    )

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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_build_message(mock_memory_instance, seed_group):
    # This test is obsolete since _build_message was removed and message preparation
    # is now done inline in send_prompt_async. The functionality is tested by
    # other send_prompt_async tests that verify message pieces have correct
    # conversation_id, sequence, and role values.
    pass


@pytest.mark.asyncio
async def test_convert_response_values_index(mock_memory_instance, response: Message):
    response_converter = PromptConverterConfiguration(converters=[Base64Converter()], indexes_to_apply=[0])

    normalizer = PromptNormalizer()

    await normalizer.convert_values(converter_configurations=[response_converter], message=response)
    assert response.get_value() == "SGVsbG8=", "Converter should be applied here"
    assert response.get_value(1) == "part 2", "Converter should not be applied since we specified only 0"


@pytest.mark.asyncio
async def test_convert_response_values_type(mock_memory_instance, response: Message):
    response_converter = PromptConverterConfiguration(
        converters=[Base64Converter()], prompt_data_types_to_apply=["text"]
    )

    normalizer = PromptNormalizer()

    await normalizer.convert_values(converter_configurations=[response_converter], message=response)
    assert response.get_value() == "SGVsbG8="
    assert response.get_value(1) == "cGFydCAy"


@pytest.mark.asyncio
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
        == mock_memory_instance.add_message_to_memory.call_args_list[0][1]["request"].message_pieces[0].original_value
    )
    assert (
        "Test Exception"
        in mock_memory_instance.add_message_to_memory.call_args_list[1][1]["request"].message_pieces[0].original_value
    )


# Tests for execution context in converter operations (used for error message handling)


class ContextCapturingConverter(PromptConverter):
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


class FailingConverter(PromptConverter):
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

    @pytest.mark.asyncio
    async def test_convert_values_sets_converter_context(self, mock_memory_instance):
        """Test that convert_values sets CONVERTER execution context."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = PromptConverterConfiguration(converters=[ContextCapturingConverter()])

        await normalizer.convert_values(converter_configurations=[converter_config], message=message)

        # The converter should have captured the execution context
        captured = ContextCapturingConverter.captured_context
        assert captured is not None
        assert captured.component_role == ComponentRole.CONVERTER

    @pytest.mark.asyncio
    async def test_convert_values_inherits_outer_context(self, mock_memory_instance):
        """Test that converter context inherits attack info from outer context."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = PromptConverterConfiguration(converters=[ContextCapturingConverter()])

        # Set an outer execution context (simulating being called from an attack)
        with execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="TestAttack",
            attack_identifier=get_mock_attack_identifier("TestAttack"),
            objective_target_conversation_id="conv-456",
        ):
            await normalizer.convert_values(converter_configurations=[converter_config], message=message)

        # The converter should have captured the context with inherited values
        captured = ContextCapturingConverter.captured_context
        assert captured is not None
        assert captured.component_role == ComponentRole.CONVERTER
        assert captured.attack_strategy_name == "TestAttack"
        assert captured.objective_target_conversation_id == "conv-456"

    @pytest.mark.asyncio
    async def test_convert_values_exception_propagates(self, mock_memory_instance):
        """Test that converter exceptions propagate correctly."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter_config = PromptConverterConfiguration(converters=[FailingConverter()])

        with pytest.raises(RuntimeError, match="Converter failed"):
            await normalizer.convert_values(converter_configurations=[converter_config], message=message)

    @pytest.mark.asyncio
    async def test_convert_values_context_includes_converter_identifier(self, mock_memory_instance):
        """Test that converter context includes the converter's identifier."""
        normalizer = PromptNormalizer()
        message = Message.from_prompt(prompt="test", role="user")

        converter = ContextCapturingConverter()
        converter_config = PromptConverterConfiguration(converters=[converter])

        await normalizer.convert_values(converter_configurations=[converter_config], message=message)

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


@pytest.mark.asyncio
async def test_add_prepended_conversation_to_memory(mock_memory_instance):
    normalizer = PromptNormalizer()
    conv_id = "test-conv-id"
    attack_id = get_mock_attack_identifier()

    piece = MessagePiece(role="user", original_value="prepended text", conversation_id="old-id")
    message = Message(message_pieces=[piece])

    result = await normalizer.add_prepended_conversation_to_memory(
        conversation_id=conv_id,
        should_convert=False,
        attack_identifier=attack_id,
        prepended_conversation=[message],
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].message_pieces[0].conversation_id == conv_id
    assert result[0].message_pieces[0].attack_identifier == attack_id
    mock_memory_instance.add_message_to_memory.assert_called_once()
