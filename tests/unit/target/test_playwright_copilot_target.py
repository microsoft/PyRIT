# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyrit.models import (
    Message,
    MessagePiece,
)
from pyrit.prompt_target.playwright_copilot_target import (
    CopilotSelectors,
    CopilotType,
    PlaywrightCopilotTarget,
)


@pytest.mark.usefixtures("patch_central_database")
class TestPlaywrightCopilotTarget:
    """Test suite for PlaywrightCopilotTarget class."""

    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page object."""
        page = AsyncMock(name="MockPage")
        page.url = "https://copilot.microsoft.com/"
        # Make locator a regular method that returns an AsyncMock
        page.locator = MagicMock(return_value=AsyncMock())
        page.eval_on_selector_all = AsyncMock()
        page.click = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock()

        # Create a simple mock for expect_file_chooser - we'll override this in specific tests
        page.expect_file_chooser = MagicMock()
        return page

    @pytest.fixture
    def mock_m365_page(self):
        """Create a mock Playwright page object for M365 Copilot."""
        page = AsyncMock(name="MockM365Page")
        page.url = "https://m365.microsoft.com/copilot"
        # Make locator a regular method that returns an AsyncMock
        page.locator = MagicMock(return_value=AsyncMock())
        page.eval_on_selector_all = AsyncMock()
        page.click = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock()

        # Create a simple mock for expect_file_chooser - we'll override this in specific tests
        page.expect_file_chooser = MagicMock()
        return page
        return page

    @pytest.fixture
    def text_request_piece(self):
        """Create a sample text request piece."""
        return MessagePiece(
            role="user",
            converted_value="Hello, how are you?",
            original_value="Hello, how are you?",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="test-conversation-id",
        )

    @pytest.fixture
    def image_request_piece(self):
        """Create a sample image request piece."""
        return MessagePiece(
            role="user",
            converted_value="/path/to/image.jpg",
            original_value="/path/to/image.jpg",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
            conversation_id="test-conversation-id",
        )

    @pytest.fixture
    def multimodal_request(self, text_request_piece, image_request_piece):
        """Create a multimodal request with text and image."""
        return Message(message_pieces=[text_request_piece, image_request_piece])

    def test_init_consumer_copilot(self, mock_page):
        """Test initialization with Consumer Copilot."""
        target = PlaywrightCopilotTarget(page=mock_page, copilot_type=CopilotType.CONSUMER)

        assert target._page == mock_page
        assert target._type == CopilotType.CONSUMER

    def test_init_m365_copilot(self, mock_m365_page):
        """Test initialization with M365 Copilot."""
        target = PlaywrightCopilotTarget(page=mock_m365_page, copilot_type=CopilotType.M365)

        assert target._page == mock_m365_page
        assert target._type == CopilotType.M365

    def test_init_default_consumer_type(self, mock_page):
        """Test that Consumer is the default Copilot type."""
        target = PlaywrightCopilotTarget(page=mock_page)

        assert target._type == CopilotType.CONSUMER

    def test_init_url_type_mismatch_m365_as_consumer(self, mock_m365_page):
        """Test error when M365 URL is used with consumer type."""
        with pytest.raises(
            ValueError, match="The provided page URL indicates M365 Copilot, but the type is set to consumer"
        ):
            PlaywrightCopilotTarget(page=mock_m365_page, copilot_type=CopilotType.CONSUMER)

    def test_init_url_type_mismatch_consumer_as_m365(self, mock_page):
        """Test error when consumer URL is used with M365 type."""
        with pytest.raises(
            ValueError, match="The provided page URL does not indicate M365 Copilot, but the type is set to m365"
        ):
            PlaywrightCopilotTarget(page=mock_page, copilot_type=CopilotType.M365)

    def test_get_selectors_consumer(self, mock_page):
        """Test selector configuration for Consumer Copilot."""
        target = PlaywrightCopilotTarget(page=mock_page, copilot_type=CopilotType.CONSUMER)
        selectors = target._get_selectors()

        assert selectors.input_selector == "#userInput"
        assert selectors.send_button_selector == 'button[data-testid="submit-button"]'
        assert selectors.ai_messages_selector == 'div[data-content="ai-message"]'
        assert selectors.plus_button_dropdown_selector == 'button[aria-label="Open"]'
        assert selectors.file_picker_selector == 'button[aria-label="Add images or files"]'

    def test_get_selectors_m365(self, mock_m365_page):
        """Test selector configuration for M365 Copilot."""
        target = PlaywrightCopilotTarget(page=mock_m365_page, copilot_type=CopilotType.M365)
        selectors = target._get_selectors()

        assert selectors.input_selector == 'span[role="textbox"][contenteditable="true"][aria-label="Message Copilot"]'
        assert selectors.send_button_selector == 'button[type="submit"]'
        assert selectors.ai_messages_selector == 'div[data-testid="copilot-message-div"]'
        assert selectors.plus_button_dropdown_selector == 'button[aria-label="Add content"]'
        assert selectors.file_picker_selector == 'span.fui-MenuItem__content:has-text("Upload images and files")'

    def test_validate_request_unsupported_type(self, mock_page):
        """Test validation with unsupported data type."""
        target = PlaywrightCopilotTarget(page=mock_page)
        unsupported_piece = MessagePiece(
            role="user",
            converted_value="some audio data",
            original_value="some audio data",
            original_value_data_type="audio_path",
            converted_value_data_type="audio_path",
        )
        request = Message(message_pieces=[unsupported_piece])

        with pytest.raises(
            ValueError, match=r"This target only supports .* prompt input\. Piece 0 has type: audio_path\."
        ):
            target._validate_request(message=request)

    def test_validate_request_valid_text(self, mock_page, text_request_piece):
        """Test validation with valid text request."""
        target = PlaywrightCopilotTarget(page=mock_page)
        request = Message(message_pieces=[text_request_piece])

        # Should not raise any exception
        target._validate_request(message=request)

    def test_validate_request_valid_multimodal(self, mock_page, multimodal_request):
        """Test validation with valid multimodal request."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Should not raise any exception
        target._validate_request(message=multimodal_request)

    @pytest.mark.asyncio
    async def test_send_text_async(self, mock_page):
        """Test sending text input."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        mock_page.locator.return_value = mock_locator

        await target._send_text_async(text="Hello world", input_selector="#test-input")

        # locator should be called twice - once for click, once for type
        assert mock_page.locator.call_count == 2
        # Check that both calls were with the same selector
        expected_calls = [call("#test-input"), call("#test-input")]
        actual_locator_calls = list(mock_page.locator.call_args_list)
        assert actual_locator_calls == expected_calls

        mock_locator.click.assert_awaited_once()
        mock_locator.type.assert_awaited_once_with("Hello world")

    @pytest.mark.asyncio
    async def test_click_dropdown_button_async_success_first_try(self, mock_page):
        """Test successful dropdown button click on first attempt."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        mock_locator.count.return_value = 1
        mock_page.locator.return_value = mock_locator

        await target._click_dropdown_button_async("#test-button")

        mock_page.locator.assert_called_once_with("#test-button")
        mock_locator.count.assert_awaited_once()
        mock_locator.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_dropdown_button_async_success_after_retry(self, mock_page):
        """Test successful dropdown button click after retries."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        # First two attempts fail, third succeeds
        mock_locator.count.side_effect = [0, 0, 1]
        mock_page.locator.return_value = mock_locator

        await target._click_dropdown_button_async("#test-button")

        assert mock_locator.count.call_count == 3
        mock_locator.click.assert_awaited_once()
        # Should have waited twice before success
        assert mock_page.wait_for_timeout.call_count == 2

    @pytest.mark.asyncio
    async def test_click_dropdown_button_async_failure(self, mock_page):
        """Test dropdown button click failure after all retries."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        mock_locator.count.return_value = 0  # Always fails
        mock_page.locator.return_value = mock_locator

        with pytest.raises(RuntimeError, match="Could not find button to open the dropdown for uploading an image"):
            await target._click_dropdown_button_async("#test-button")

        # Should retry 5 times
        assert mock_locator.count.call_count == 5
        mock_locator.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_login_requirement_async_no_login_needed(self, mock_page):
        """Test no login requirement check."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        mock_locator.count.return_value = 0  # No sign-in header
        mock_page.locator.return_value = mock_locator

        # Should not raise any exception
        await target._check_login_requirement_async()

        mock_page.locator.assert_called_once_with('h1:has-text("Sign in for the full experience")')

    @pytest.mark.asyncio
    async def test_check_login_requirement_async_login_required(self, mock_page):
        """Test login requirement detected."""
        target = PlaywrightCopilotTarget(page=mock_page)
        mock_locator = AsyncMock()
        mock_locator.count.return_value = 1  # Sign-in header present
        mock_page.locator.return_value = mock_locator

        with pytest.raises(RuntimeError, match="Login required to access advanced features in Consumer Copilot"):
            await target._check_login_requirement_async()

    @pytest.mark.asyncio
    async def test_upload_image_async_success(self, mock_page):
        """Test successful image upload."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Mock selectors and dropdown interaction
        dropdown_locator = AsyncMock()
        dropdown_locator.count.return_value = 1
        file_picker_locator = AsyncMock()
        login_check_locator = AsyncMock()
        login_check_locator.count.return_value = 0  # No login required

        mock_page.locator.side_effect = [dropdown_locator, file_picker_locator, login_check_locator]

        # Mock the file chooser with a proper implementation
        mock_file_chooser = AsyncMock()

        # Create a mock for the context manager that properly implements the await pattern
        class MockFileChooserInfo:
            def __init__(self, file_chooser):
                self._file_chooser = file_chooser

            @property
            def value(self):
                # Return a completed coroutine
                import asyncio

                future = asyncio.Future()
                future.set_result(self._file_chooser)
                return future

        class MockFileChooserContextManager:
            def __init__(self, file_chooser):
                self._file_chooser = file_chooser

            async def __aenter__(self):
                return MockFileChooserInfo(self._file_chooser)

            async def __aexit__(self, *args):
                pass

        mock_page.expect_file_chooser = MagicMock(return_value=MockFileChooserContextManager(mock_file_chooser))

        await target._upload_image_async("/path/to/image.jpg")

        dropdown_locator.click.assert_awaited_once()
        file_picker_locator.wait_for.assert_awaited_once_with(state="visible", timeout=5000)
        file_picker_locator.click.assert_awaited_once()
        mock_file_chooser.set_files.assert_awaited_once_with("/path/to/image.jpg")

    @pytest.mark.asyncio
    async def test_wait_for_response_async_success(self, mock_page):
        """Test successful response waiting."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock initial and final message counts
        mock_page.eval_on_selector_all.side_effect = [2, 3]  # 2 initial, 3 after response
        mock_page.click = AsyncMock()
        mock_page.query_selector_all.return_value = [AsyncMock()]

        # Mock response extraction
        with patch.object(target, "_extract_multimodal_content_async", return_value="Response text") as mock_extract:
            result = await target._wait_for_response_async(selectors)

        assert result == "Response text"
        mock_page.click.assert_awaited_once_with(selectors.send_button_selector)
        mock_extract.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wait_for_response_async_timeout(self, mock_page):
        """Test response waiting timeout."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock message count that never increases
        mock_page.eval_on_selector_all.return_value = 2  # Never increases
        mock_page.click = AsyncMock()
        mock_page.query_selector_all.return_value = [AsyncMock()]

        # Mock time to trigger timeout quickly - need many values for the loop
        time_values = [0, 0] + [i * 10 for i in range(50)]  # Start at 0, then advance
        with patch("time.time", side_effect=time_values):
            with pytest.raises(TimeoutError, match="Timed out waiting for AI response"):
                await target._wait_for_response_async(selectors)

    @pytest.mark.asyncio
    async def test_send_prompt_async_text_only(self, mock_page, text_request_piece):
        """Test sending text-only prompt."""
        target = PlaywrightCopilotTarget(page=mock_page)
        request = Message(message_pieces=[text_request_piece])

        # Mock the interaction method
        with patch.object(target, "_interact_with_copilot_async", return_value="AI response") as mock_interact:
            response = await target.send_prompt_async(message=request)

        assert len(response) == 1
        mock_interact.assert_awaited_once_with(request)
        assert response[0].message_pieces[0].converted_value == "AI response"
        assert response[0].message_pieces[0].api_role == "assistant"

    @pytest.mark.asyncio
    async def test_no_page(self, text_request_piece):
        """Test error when page is not initialized."""
        with pytest.raises(RuntimeError, match="Playwright page is not initialized"):
            PlaywrightCopilotTarget(page=None)

    @pytest.mark.asyncio
    async def test_send_prompt_async_interaction_error(self, mock_page, text_request_piece):
        """Test error handling during interaction."""
        target = PlaywrightCopilotTarget(page=mock_page)
        request = Message(message_pieces=[text_request_piece])

        # Mock the interaction method to raise an exception
        with patch.object(target, "_interact_with_copilot_async", side_effect=Exception("Interaction failed")):
            with pytest.raises(RuntimeError, match="An error occurred during interaction: Interaction failed"):
                await target.send_prompt_async(message=request)

    @pytest.mark.asyncio
    async def test_interact_with_copilot_async_multimodal(self, mock_page, multimodal_request):
        """Test multimodal interaction with both text and image."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Mock the helper methods
        with (
            patch.object(target, "_send_text_async") as mock_send_text,
            patch.object(target, "_upload_image_async") as mock_upload_image,
            patch.object(target, "_wait_for_response_async", return_value="AI response") as mock_wait,
        ):
            result = await target._interact_with_copilot_async(multimodal_request)

        # Verify text and image handling
        mock_send_text.assert_awaited_once()
        mock_upload_image.assert_awaited_once_with("/path/to/image.jpg")
        mock_wait.assert_awaited_once()
        assert result == "AI response"

    def test_constants(self, mock_page):
        """Test that class constants are defined correctly."""
        target = PlaywrightCopilotTarget(page=mock_page)

        assert target.MAX_WAIT_TIME_SECONDS == 300
        assert target.RESPONSE_COMPLETE_WAIT_MS == 3000
        assert target.POLL_INTERVAL_MS == 2000
        assert target.RETRY_ATTEMPTS == 5
        assert target.RETRY_DELAY_MS == 500
        assert {"text", "image_path"} == target.SUPPORTED_DATA_TYPES


@pytest.mark.usefixtures("patch_central_database")
class TestCopilotSelectors:
    """Test suite for CopilotSelectors dataclass."""

    def test_copilot_selectors_creation(self):
        """Test CopilotSelectors dataclass creation."""
        selectors = CopilotSelectors(
            input_selector="#input",
            send_button_selector="#send",
            ai_messages_selector=".messages",
            ai_messages_group_selector=".message-group",
            text_content_selector=".text",
            plus_button_dropdown_selector="#plus",
            file_picker_selector="#files",
        )

        assert selectors.input_selector == "#input"
        assert selectors.send_button_selector == "#send"
        assert selectors.ai_messages_selector == ".messages"
        assert selectors.ai_messages_group_selector == ".message-group"
        assert selectors.text_content_selector == ".text"
        assert selectors.plus_button_dropdown_selector == "#plus"
        assert selectors.file_picker_selector == "#files"


@pytest.mark.usefixtures("patch_central_database")
class TestCopilotType:
    """Test suite for CopilotType enum."""

    def test_copilot_type_values(self):
        """Test CopilotType enum values."""
        assert CopilotType.CONSUMER.value == "consumer"
        assert CopilotType.M365.value == "m365"

    def test_copilot_type_comparison(self):
        """Test CopilotType enum comparison."""
        assert CopilotType.CONSUMER != CopilotType.M365
        assert CopilotType.CONSUMER == CopilotType.CONSUMER


@pytest.mark.usefixtures("patch_central_database")
class TestPlaywrightCopilotTargetMultimodal:
    """Test suite for multimodal functionality in PlaywrightCopilotTarget."""

    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page object."""
        page = AsyncMock(name="MockPage")
        page.url = "https://copilot.microsoft.com/"
        page.locator = MagicMock(return_value=AsyncMock())
        page.eval_on_selector_all = AsyncMock()
        page.click = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock()
        page.expect_file_chooser = MagicMock()
        return page

    @pytest.fixture
    def text_request_piece(self):
        """Create a sample text request piece."""
        return MessagePiece(
            role="user",
            converted_value="Hello, how are you?",
            original_value="Hello, how are you?",
            original_value_data_type="text",
            converted_value_data_type="text",
        )

    @pytest.fixture
    def image_request_piece(self):
        """Create a sample image request piece."""
        return MessagePiece(
            role="user",
            converted_value="/path/to/image.jpg",
            original_value="/path/to/image.jpg",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )

    @pytest.mark.asyncio
    async def test_extract_text_from_message_groups(self, mock_page):
        """Test extracting text from message groups."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Mock message groups with text elements
        mock_group1 = AsyncMock()
        mock_text_elem1 = AsyncMock()
        mock_text_elem1.text_content.return_value = "Hello "
        mock_text_elem2 = AsyncMock()
        mock_text_elem2.text_content.return_value = "world!"
        mock_group1.query_selector_all.return_value = [mock_text_elem1, mock_text_elem2]

        mock_group2 = AsyncMock()
        mock_text_elem3 = AsyncMock()
        mock_text_elem3.text_content.return_value = "How are you?"
        mock_group2.query_selector_all.return_value = [mock_text_elem3]

        ai_message_groups = [mock_group1, mock_group2]

        result = await target._extract_text_from_message_groups(ai_message_groups, "p > span")

        assert result == ["Hello", "world!", "How are you?"]
        assert mock_group1.query_selector_all.call_count == 1
        assert mock_group2.query_selector_all.call_count == 1

    @pytest.mark.asyncio
    async def test_extract_text_from_message_groups_empty(self, mock_page):
        """Test extracting text when no text elements found."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = []

        result = await target._extract_text_from_message_groups([mock_group], "p > span")

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_text_from_message_groups_with_none_content(self, mock_page):
        """Test extracting text when elements return None content."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_group = AsyncMock()
        mock_text_elem = AsyncMock()
        mock_text_elem.text_content.return_value = None
        mock_group.query_selector_all.return_value = [mock_text_elem]

        result = await target._extract_text_from_message_groups([mock_group], "p > span")

        assert result == []

    def test_filter_placeholder_text(self, mock_page):
        """Test filtering out placeholder text."""
        target = PlaywrightCopilotTarget(page=mock_page)

        text_parts = ["Hello", "generating response", "world", "Thinking", "How are you?", "GENERATING"]
        result = target._filter_placeholder_text(text_parts)

        assert result == ["Hello", "world", "How are you?"]

    def test_filter_placeholder_text_all_placeholders(self, mock_page):
        """Test filtering when all text is placeholder."""
        target = PlaywrightCopilotTarget(page=mock_page)

        text_parts = ["generating response", "thinking", "generating"]
        result = target._filter_placeholder_text(text_parts)

        assert result == []

    def test_filter_placeholder_text_empty_list(self, mock_page):
        """Test filtering with empty input."""
        target = PlaywrightCopilotTarget(page=mock_page)

        result = target._filter_placeholder_text([])

        assert result == []

    @pytest.mark.asyncio
    async def test_count_images_in_groups_with_iframes(self, mock_page):
        """Test counting images in message groups with iframes."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Mock iframe with images
        mock_iframe = AsyncMock()
        mock_content_frame = AsyncMock()
        mock_img1 = AsyncMock()
        mock_img2 = AsyncMock()
        mock_content_frame.query_selector_all.return_value = [mock_img1, mock_img2]
        mock_iframe.content_frame.return_value = mock_content_frame

        mock_group = AsyncMock()
        mock_group.query_selector_all.side_effect = [[mock_iframe], []]  # iframes query  # direct images query

        result = await target._count_images_in_groups([mock_group])

        assert result == 2

    @pytest.mark.asyncio
    async def test_count_images_in_groups_direct_images(self, mock_page):
        """Test counting direct images without iframes."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_img1 = AsyncMock()
        mock_img2 = AsyncMock()
        mock_img3 = AsyncMock()

        mock_group = AsyncMock()
        mock_group.query_selector_all.side_effect = [
            [],  # no iframes
            [mock_img1, mock_img2, mock_img3],  # direct images
        ]

        result = await target._count_images_in_groups([mock_group])

        assert result == 3

    @pytest.mark.asyncio
    async def test_count_images_in_groups_no_images(self, mock_page):
        """Test counting when no images found."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_group = AsyncMock()
        mock_group.query_selector_all.side_effect = [[], []]

        result = await target._count_images_in_groups([mock_group])

        assert result == 0

    @pytest.mark.asyncio
    async def test_count_images_in_groups_iframe_error(self, mock_page):
        """Test counting images when iframe access fails."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_iframe = AsyncMock()
        mock_iframe.content_frame.side_effect = Exception("Cannot access iframe")

        mock_group = AsyncMock()
        mock_group.query_selector_all.side_effect = [[mock_iframe], []]  # iframe that will fail  # no direct images

        result = await target._count_images_in_groups([mock_group])

        assert result == 0

    @pytest.mark.asyncio
    async def test_wait_minimum_time(self, mock_page):
        """Test minimum wait time function."""
        target = PlaywrightCopilotTarget(page=mock_page)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await target._wait_minimum_time(3)

            assert mock_sleep.call_count == 3
            mock_sleep.assert_has_calls([call(1), call(1), call(1)])

    @pytest.mark.asyncio
    async def test_extract_images_from_iframes(self, mock_page):
        """Test extracting images from iframes."""
        target = PlaywrightCopilotTarget(page=mock_page)

        # Mock iframe with images
        mock_iframe = AsyncMock()
        mock_iframe.get_attribute.return_value = "iframe-123"
        mock_content_frame = AsyncMock()
        mock_img1 = AsyncMock()
        mock_img2 = AsyncMock()
        mock_content_frame.query_selector_all.return_value = [mock_img1, mock_img2]
        mock_iframe.content_frame.return_value = mock_content_frame

        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = [mock_iframe]

        result = await target._extract_images_from_iframes([mock_group])

        assert len(result) == 2
        assert result == [mock_img1, mock_img2]

    @pytest.mark.asyncio
    async def test_extract_images_from_iframes_no_content_frame(self, mock_page):
        """Test extracting images when iframe has no content frame."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_iframe = AsyncMock()
        mock_iframe.get_attribute.return_value = "iframe-123"
        mock_iframe.content_frame.return_value = None

        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = [mock_iframe]

        result = await target._extract_images_from_iframes([mock_group])

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_images_from_iframes_exception(self, mock_page):
        """Test extracting images when iframe access raises exception."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_iframe = AsyncMock()
        mock_iframe.get_attribute.side_effect = Exception("Access denied")

        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = [mock_iframe]

        result = await target._extract_images_from_iframes([mock_group])

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_images_from_message_groups(self, mock_page):
        """Test extracting images directly from message groups."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        mock_img1 = AsyncMock()
        mock_img2 = AsyncMock()
        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = [mock_img1, mock_img2]

        mock_page.query_selector_all.return_value = []

        result = await target._extract_images_from_message_groups(selectors, [mock_group])

        assert len(result) == 2
        assert result == [mock_img1, mock_img2]

    @pytest.mark.asyncio
    async def test_extract_images_from_message_groups_fallback_ai_messages(self, mock_page):
        """Test fallback to AI messages selector when no images in groups."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # No images in message groups
        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = []

        # But images in AI messages
        mock_ai_message = AsyncMock()
        mock_img = AsyncMock()
        mock_ai_message.query_selector_all.return_value = [mock_img]

        mock_page.query_selector_all.return_value = [mock_ai_message]

        result = await target._extract_images_from_message_groups(selectors, [mock_group])

        assert len(result) == 1
        assert result == [mock_img]

    @pytest.mark.asyncio
    async def test_extract_images_from_message_groups_generic_selector(self, mock_page):
        """Test fallback to generic img selector."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # No images in message groups
        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = []

        # Mock AI message that fails button selector but has generic images
        mock_ai_message = AsyncMock()
        mock_img = AsyncMock()
        mock_ai_message.query_selector_all.side_effect = [[], [mock_img]]

        mock_page.query_selector_all.return_value = [mock_ai_message]

        result = await target._extract_images_from_message_groups(selectors, [mock_group])

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_process_image_elements_with_data_url(self, mock_page):
        """Test processing image elements with data URLs."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_img = AsyncMock()
        mock_img.get_attribute.return_value = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        mock_serializer = MagicMock()
        mock_serializer.value = "/saved/image/path.png"
        mock_serializer.save_b64_image = AsyncMock()

        with patch(
            "pyrit.prompt_target.playwright_copilot_target.data_serializer_factory", return_value=mock_serializer
        ):
            result = await target._process_image_elements([mock_img])

        assert len(result) == 1
        assert result[0] == ("/saved/image/path.png", "image_path")
        mock_serializer.save_b64_image.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_image_elements_non_data_url(self, mock_page):
        """Test processing image elements with non-data URLs."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_img = AsyncMock()
        mock_img.get_attribute.return_value = "https://example.com/image.png"

        result = await target._process_image_elements([mock_img])

        assert result == []

    @pytest.mark.asyncio
    async def test_process_image_elements_no_src(self, mock_page):
        """Test processing image elements with no src attribute."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_img = AsyncMock()
        mock_img.get_attribute.return_value = None

        result = await target._process_image_elements([mock_img])

        assert result == []

    @pytest.mark.asyncio
    async def test_process_image_elements_exception(self, mock_page):
        """Test processing image elements when exception occurs."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_img = AsyncMock()
        mock_img.get_attribute.return_value = "data:image/png;base64,invalid"

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image = AsyncMock(side_effect=Exception("Save failed"))

        with patch(
            "pyrit.prompt_target.playwright_copilot_target.data_serializer_factory", return_value=mock_serializer
        ):
            result = await target._process_image_elements([mock_img])

        assert result == []

    @pytest.mark.asyncio
    async def test_wait_for_images_to_stabilize_images_found(self, mock_page):
        """Test waiting for images when images appear."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock initial groups
        initial_groups = [AsyncMock(), AsyncMock()]

        # Mock new groups being added
        mock_new_group1 = AsyncMock()
        mock_new_group2 = AsyncMock()
        all_groups_after_wait = initial_groups + [mock_new_group1, mock_new_group2]

        mock_page.query_selector_all.return_value = all_groups_after_wait

        # Mock image count - no images initially, then images appear
        with patch.object(target, "_count_images_in_groups", side_effect=[0, 0, 0, 2]):
            with patch.object(target, "_wait_minimum_time", new_callable=AsyncMock) as mock_min_wait:
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await target._wait_for_images_to_stabilize(selectors, initial_groups, 2)

        mock_min_wait.assert_awaited_once_with(3)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_wait_for_images_to_stabilize_dom_stabilizes(self, mock_page):
        """Test waiting when DOM stabilizes without finding images."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        initial_groups = [AsyncMock()]
        mock_new_group = AsyncMock()
        all_groups = initial_groups + [mock_new_group]

        # Return same group count repeatedly
        mock_page.query_selector_all.return_value = all_groups

        with patch.object(target, "_count_images_in_groups", return_value=0):
            with patch.object(target, "_wait_minimum_time", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await target._wait_for_images_to_stabilize(selectors, initial_groups, 1)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_text_only(self, mock_page):
        """Test extracting multimodal content with text only."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock message group with text
        mock_group = AsyncMock()
        mock_text_elem = AsyncMock()
        mock_text_elem.text_content.return_value = "Hello world"
        mock_group.query_selector_all.return_value = [mock_text_elem]

        mock_page.query_selector_all.return_value = [mock_group]

        with patch.object(target, "_wait_for_images_to_stabilize", return_value=[mock_group]):
            with patch.object(target, "_extract_images_from_iframes", return_value=[]):
                with patch.object(target, "_extract_images_from_message_groups", return_value=[]):
                    result = await target._extract_multimodal_content_async(selectors, 0)

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_text_and_images(self, mock_page):
        """Test extracting multimodal content with both text and images."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock message group with text
        mock_group = AsyncMock()
        mock_text_elem = AsyncMock()
        mock_text_elem.text_content.return_value = "Check this image"
        mock_group.query_selector_all.return_value = [mock_text_elem]

        mock_page.query_selector_all.return_value = [mock_group]

        # Mock image extraction
        mock_img = AsyncMock()

        with patch.object(target, "_wait_for_images_to_stabilize", return_value=[mock_group]):
            with patch.object(target, "_extract_images_from_iframes", return_value=[mock_img]):
                with patch.object(target, "_process_image_elements", return_value=[("/path/image.png", "image_path")]):
                    result = await target._extract_multimodal_content_async(selectors, 0)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == ("Check this image", "text")
        assert result[1] == ("/path/image.png", "image_path")

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_images_only(self, mock_page):
        """Test extracting multimodal content with images only."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock message group with no text
        mock_group = AsyncMock()
        mock_group.query_selector_all.return_value = []

        mock_page.query_selector_all.return_value = [mock_group]

        # Mock image extraction
        mock_img = AsyncMock()

        with patch.object(target, "_wait_for_images_to_stabilize", return_value=[mock_group]):
            with patch.object(target, "_extract_images_from_iframes", return_value=[mock_img]):
                with patch.object(target, "_process_image_elements", return_value=[("/path/image.png", "image_path")]):
                    result = await target._extract_multimodal_content_async(selectors, 0)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == ("/path/image.png", "image_path")

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_placeholder_text(self, mock_page):
        """Test extracting content when only placeholder text exists."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock message group with placeholder text
        mock_group = AsyncMock()
        mock_text_elem = AsyncMock()
        mock_text_elem.text_content.return_value = "generating response"
        mock_group.query_selector_all.return_value = [mock_text_elem]
        # Fix: text_content() should return a string, not a coroutine
        mock_group.text_content = AsyncMock(return_value="Fallback text content")

        mock_page.query_selector_all.return_value = [mock_group]

        with patch.object(target, "_wait_for_images_to_stabilize", return_value=[mock_group]):
            with patch.object(target, "_extract_images_from_iframes", return_value=[]):
                with patch.object(target, "_extract_images_from_message_groups", return_value=[]):
                    result = await target._extract_multimodal_content_async(selectors, 0)

        # Should fall back to text_content
        assert isinstance(result, str)
        assert result == "Fallback text content"

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_no_groups(self, mock_page):
        """Test extracting content when no message groups found."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        mock_page.query_selector_all.return_value = []

        result = await target._extract_multimodal_content_async(selectors, 0)

        assert result == ""

    @pytest.mark.asyncio
    async def test_extract_multimodal_content_with_initial_group_count(self, mock_page):
        """Test extracting content with initial group count filtering."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock 3 total groups, but we want to skip first 2
        mock_old_group1 = AsyncMock()
        mock_old_group2 = AsyncMock()
        mock_new_group = AsyncMock()
        mock_text_elem = AsyncMock()
        mock_text_elem.text_content.return_value = "New response"
        mock_new_group.query_selector_all.return_value = [mock_text_elem]

        all_groups = [mock_old_group1, mock_old_group2, mock_new_group]
        mock_page.query_selector_all.return_value = all_groups

        with patch.object(target, "_wait_for_images_to_stabilize", return_value=[mock_new_group]):
            with patch.object(target, "_extract_images_from_iframes", return_value=[]):
                with patch.object(target, "_extract_images_from_message_groups", return_value=[]):
                    result = await target._extract_multimodal_content_async(selectors, 2)

        assert result == "New response"

    @pytest.mark.asyncio
    async def test_send_prompt_async_multimodal_response(self, mock_page):
        """Test sending prompt and receiving multimodal response."""
        target = PlaywrightCopilotTarget(page=mock_page)

        text_piece = MessagePiece(
            role="user",
            converted_value="Show me a picture",
            original_value="Show me a picture",
            original_value_data_type="text",
            converted_value_data_type="text",
        )
        request = Message(message_pieces=[text_piece])

        # Mock multimodal response
        multimodal_content = [("Here is an image", "text"), ("/path/to/image.png", "image_path")]

        with patch.object(target, "_interact_with_copilot_async", return_value=multimodal_content):
            response = await target.send_prompt_async(message=request)

        assert len(response) == 1
        assert len(response[0].message_pieces) == 2
        assert response[0].message_pieces[0].converted_value == "Here is an image"
        assert response[0].message_pieces[0].converted_value_data_type == "text"
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].message_pieces[1].converted_value == "/path/to/image.png"
        assert response[0].message_pieces[1].converted_value_data_type == "image_path"
        assert response[0].message_pieces[1].api_role == "assistant"

    @pytest.mark.asyncio
    async def test_wait_for_response_with_placeholder_content(self, mock_page):
        """Test waiting for response when content initially has placeholders."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        # Mock initial state - need enough values for multiple loop iterations
        mock_page.eval_on_selector_all.side_effect = [
            2,  # initial AI messages count
            2,  # first check - not ready yet
            3,  # second check - new message appeared
            3,  # verification
        ]
        mock_page.query_selector_all.return_value = [AsyncMock(), AsyncMock()]

        # Mock extraction - first returns placeholder, then real content
        with patch.object(
            target,
            "_extract_multimodal_content_async",
            side_effect=[
                "generating response",  # First check - placeholder
                "Real response",  # Second check - actual content
            ],
        ) as mock_extract:
            with patch("time.time", side_effect=[0, 0, 0, 5, 5, 5, 5, 10, 10]):  # Advance time
                result = await target._wait_for_response_async(selectors)

        assert result == "Real response"
        # Should have been called twice - once for placeholder, once for real content
        assert mock_extract.call_count == 2

    @pytest.mark.asyncio
    async def test_wait_for_response_with_multimodal_list_ready(self, mock_page):
        """Test waiting for response when multimodal list is ready."""
        target = PlaywrightCopilotTarget(page=mock_page)
        selectors = target._get_selectors()

        mock_page.eval_on_selector_all.side_effect = [2, 3]
        mock_page.query_selector_all.return_value = [AsyncMock()]

        multimodal_result = [("Text", "text"), ("/image.png", "image_path")]

        with patch.object(target, "_extract_multimodal_content_async", return_value=multimodal_result):
            result = await target._wait_for_response_async(selectors)

        assert result == multimodal_result

    @pytest.mark.asyncio
    async def test_click_dropdown_button_visibility_check(self, mock_page):
        """Test dropdown button click with visibility and enabled checks."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_locator = AsyncMock()
        mock_locator.count.return_value = 1
        mock_first = AsyncMock()
        mock_first.is_visible.return_value = True
        mock_first.is_enabled.return_value = True
        mock_locator.first = mock_first

        mock_page.locator.return_value = mock_locator

        await target._click_dropdown_button_async("#test-button")

        mock_first.is_visible.assert_awaited_once()
        mock_first.is_enabled.assert_awaited_once()
        mock_locator.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_dropdown_button_not_visible(self, mock_page):
        """Test dropdown button click when button never becomes visible."""
        target = PlaywrightCopilotTarget(page=mock_page)

        mock_locator = AsyncMock()
        mock_locator.count.return_value = 1
        mock_first = AsyncMock()
        # Button never becomes visible in 5 attempts
        mock_first.is_visible.return_value = False
        mock_first.is_enabled.return_value = True
        mock_locator.first = mock_first

        mock_page.locator.return_value = mock_locator

        with pytest.raises(RuntimeError, match="Could not find button to open the dropdown"):
            await target._click_dropdown_button_async("#test-button")
