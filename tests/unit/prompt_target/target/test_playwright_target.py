# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import MutableSequence
from unittest.mock import AsyncMock

import pytest

from pyrit.models import (
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target import PlaywrightTarget


@pytest.mark.usefixtures("patch_central_database")
class TestPlaywrightTarget:
    """Test suite for PlaywrightTarget class."""

    @pytest.fixture
    def sample_conversations(self) -> MutableSequence[MessagePiece]:
        conversation_1 = MessagePiece(
            role="user",
            converted_value="Hello",
            original_value="Hello",
            original_value_data_type="text",
            converted_value_data_type="text",
        )
        conversation_2 = MessagePiece(
            role="assistant",
            converted_value="World",
            original_value="World",
            original_value_data_type="text",
            converted_value_data_type="text",
        )
        return [conversation_1, conversation_2]

    @pytest.fixture
    def mock_page(self):
        page = AsyncMock(name="MockPage")
        page.url = "https://example.com/test"
        return page

    @pytest.fixture
    def mock_interaction_func(self):
        """Create a mock interaction function."""

        async def interaction_func(page, message):
            # Get the first piece's value for the mock response
            first_piece = message.message_pieces[0]
            return f"Processed: {first_piece.converted_value}"

        return AsyncMock(side_effect=interaction_func)

    @pytest.fixture
    def text_message_piece(self):
        """Create a sample text request piece."""
        return MessagePiece(
            role="user",
            converted_value="Hello, how are you?",
            original_value="Hello, how are you?",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="test-conv-id",
        )

    @pytest.fixture
    def image_message_piece(self):
        """Create a sample image request piece."""
        return MessagePiece(
            role="user",
            converted_value="/path/to/image.jpg",
            original_value="/path/to/image.jpg",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
            conversation_id="test-conv-id",
        )

    @pytest.fixture
    def multiple_text_pieces(self):
        """Create multiple text message pieces."""
        piece1 = MessagePiece(
            role="user",
            converted_value="Hello",
            original_value="Hello",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="test-conv-id",
        )
        piece2 = MessagePiece(
            role="user",
            converted_value="World",
            original_value="World",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="test-conv-id",
        )
        return [piece1, piece2]

    def test_init_with_valid_parameters(self, mock_interaction_func, mock_page):
        """Test initialization with valid parameters."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)

        assert target._interaction_func == mock_interaction_func
        assert target._page == mock_page

    def test_supported_data_types_constant(self, mock_interaction_func, mock_page):
        """Test that SUPPORTED_DATA_TYPES constant is defined correctly."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)

        assert hasattr(target, "SUPPORTED_DATA_TYPES")
        assert {"text", "image_path"} == target.SUPPORTED_DATA_TYPES

    def test_validate_request_unsupported_type(self, mock_interaction_func, mock_page):
        """Test validation with unsupported data type."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        unsupported_piece = MessagePiece(
            role="user",
            converted_value="some audio data",
            original_value="some audio data",
            original_value_data_type="audio_path",
            converted_value_data_type="audio_path",
        )
        request = Message(message_pieces=[unsupported_piece])

        with pytest.raises(
            ValueError,
            match=r"This target supports only the following data types.*If your target does support this, set the"
            r" custom_configuration parameter accordingly",
        ):
            target._validate_request(message=request)

    def test_validate_request_valid_text(self, mock_interaction_func, mock_page, text_message_piece):
        """Test validation with valid text request."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece])

        # Should not raise any exception
        target._validate_request(message=request)

    def test_validate_request_valid_image(self, mock_interaction_func, mock_page, image_message_piece):
        """Test validation with valid image request."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=[image_message_piece])

        # Should not raise any exception
        target._validate_request(message=request)

    def test_validate_request_mixed_valid_types(
        self, mock_interaction_func, mock_page, text_message_piece, image_message_piece
    ):
        """Test validation with mixed valid types."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece, image_message_piece])

        # Should not raise any exception
        target._validate_request(message=request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_single_text(self, mock_interaction_func, mock_page, text_message_piece):
        """Test sending a single text prompt."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece])

        response = await target.send_prompt_async(message=request)

        # Verify response structure
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].get_value() == "Processed: Hello, how are you?"

        # Verify interaction function was called correctly
        mock_interaction_func.assert_awaited_once_with(mock_page, request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_multiple_pieces(self, mock_interaction_func, mock_page, multiple_text_pieces):
        """Test sending multiple text prompts."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=multiple_text_pieces)

        response = await target.send_prompt_async(message=request)

        # Verify response structure
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].get_value() == "Processed: Hello"  # First piece's value

        # Verify interaction function was called with the complete request
        mock_interaction_func.assert_awaited_once_with(mock_page, request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_image_request(self, mock_interaction_func, mock_page, image_message_piece):
        """Test sending an image prompt."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)
        request = Message(message_pieces=[image_message_piece])

        response = await target.send_prompt_async(message=request)

        # Verify response structure
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].get_value() == "Processed: /path/to/image.jpg"

        # Verify interaction function was called correctly
        mock_interaction_func.assert_awaited_once_with(mock_page, request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_no_page(self, mock_interaction_func, text_message_piece):
        """Test error when page is not initialized."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=None)
        request = Message(message_pieces=[text_message_piece])

        with pytest.raises(RuntimeError, match="Playwright page is not initialized"):
            await target.send_prompt_async(message=request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_interaction_error(self, mock_page, text_message_piece):
        """Test error handling during interaction."""

        # Create a failing interaction function
        async def failing_interaction_func(page, message):
            raise Exception("Interaction failed")

        target = PlaywrightTarget(interaction_func=failing_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece])

        with pytest.raises(RuntimeError, match="An error occurred during interaction: Interaction failed"):
            await target.send_prompt_async(message=request)

    @pytest.mark.asyncio
    async def test_send_prompt_async_response_construction(self, mock_page, text_message_piece):
        """Test that response is constructed correctly."""

        # Create a custom interaction function
        async def custom_interaction_func(page, message):
            return "Custom response text"

        target = PlaywrightTarget(interaction_func=custom_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece])

        response = await target.send_prompt_async(message=request)

        # Verify response construction matches expected format
        expected_response = construct_response_from_request(
            request=text_message_piece, response_text_pieces=["Custom response text"]
        )

        assert len(response) == 1
        assert response[0].message_pieces[0].original_value == expected_response.message_pieces[0].original_value
        assert response[0].message_pieces[0].converted_value == expected_response.message_pieces[0].converted_value
        assert response[0].message_pieces[0].api_role == expected_response.message_pieces[0].api_role

    @pytest.mark.asyncio
    async def test_send_prompt_async_empty_response(self, mock_page, text_message_piece):
        """Test handling of empty response from interaction function."""

        # Create an interaction function that returns empty string
        async def empty_interaction_func(page, message):
            return ""

        target = PlaywrightTarget(interaction_func=empty_interaction_func, page=mock_page)
        request = Message(message_pieces=[text_message_piece])

        response = await target.send_prompt_async(message=request)

        # Verify empty response is handled correctly
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].get_value() == ""

    def test_protocol_interaction_function_signature(self):
        """Test that InteractionFunction protocol is properly defined."""
        from pyrit.prompt_target.playwright_target import InteractionFunction

        # Check that the protocol exists and has the right signature
        assert callable(InteractionFunction)

    @pytest.mark.asyncio
    async def test_interaction_function_receives_complete_request(self, mock_page, multiple_text_pieces):
        """Test that interaction function receives the complete Message."""
        received_request = None

        async def capture_interaction_func(page, message):
            nonlocal received_request
            received_request = message
            return "Test response"

        target = PlaywrightTarget(interaction_func=capture_interaction_func, page=mock_page)
        request = Message(message_pieces=multiple_text_pieces)

        await target.send_prompt_async(message=request)

        # Verify the interaction function received the complete request
        assert received_request is request
        assert len(received_request.message_pieces) == 2
        assert received_request.message_pieces[0].converted_value == "Hello"
        assert received_request.message_pieces[1].converted_value == "World"


# Additional edge case tests
@pytest.mark.usefixtures("patch_central_database")
class TestPlaywrightTargetEdgeCases:
    """Test edge cases and boundary conditions for PlaywrightTarget."""

    @pytest.fixture
    def mock_page(self):
        return AsyncMock(name="MockPage")

    @pytest.fixture
    def mock_interaction_func(self):
        return AsyncMock(return_value="Mock response")

    def test_validate_request_multiple_unsupported_types(self, mock_interaction_func, mock_page):
        """Test validation with multiple pieces having unsupported types."""
        target = PlaywrightTarget(interaction_func=mock_interaction_func, page=mock_page)

        unsupported_pieces = [
            MessagePiece(
                role="user",
                converted_value="audio data",
                original_value="audio data",
                original_value_data_type="audio_path",
                converted_value_data_type="audio_path",
                conversation_id="test-conv-id",
            ),
            MessagePiece(
                role="user",
                converted_value="video data",
                original_value="video data",
                original_value_data_type="video_path",
                converted_value_data_type="video_path",
                conversation_id="test-conv-id",
            ),
        ]
        request = Message(message_pieces=unsupported_pieces)

        # Should fail on the first unsupported type
        with pytest.raises(
            ValueError,
            match=r"This target supports only the following data types.*If your target does support this, set the"
            r" custom_configuration parameter accordingly",
        ):
            target._validate_request(message=request)

    @pytest.mark.asyncio
    async def test_interaction_function_with_complex_response(self, mock_page):
        """Test interaction function that returns complex response."""

        async def complex_interaction_func(page, message):
            # Simulate processing all pieces
            processed_values = [f"Processed[{piece.converted_value}]" for piece in message.message_pieces]
            return " | ".join(processed_values)

        target = PlaywrightTarget(interaction_func=complex_interaction_func, page=mock_page)

        pieces = [
            MessagePiece(
                role="user",
                converted_value="First",
                original_value="First",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id="test-conv-id",
            ),
            MessagePiece(
                role="user",
                converted_value="Second",
                original_value="Second",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id="test-conv-id",
            ),
        ]
        request = Message(message_pieces=pieces)

        response = await target.send_prompt_async(message=request)

        assert len(response) == 1
        assert response[0].get_value() == "Processed[First] | Processed[Second]"
