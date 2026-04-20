# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, List, Tuple, Union

from pyrit.models import (
    Message,
    MessagePiece,
    construct_response_from_request,
    data_serializer_factory,
)
from pyrit.models.literals import PromptDataType
from pyrit.prompt_target.common.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# Avoid errors for users who don't have playwright installed
if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = None


class CopilotType(Enum):
    """Enumeration of Copilot interface types."""

    CONSUMER = "consumer"
    M365 = "m365"


@dataclass
class CopilotSelectors:
    """Selectors for different elements in the Copilot interface."""

    input_selector: str
    send_button_selector: str
    ai_messages_selector: str
    ai_messages_group_selector: str
    text_content_selector: str
    plus_button_dropdown_selector: str
    file_picker_selector: str


class PlaywrightCopilotTarget(PromptTarget):
    """
    PlaywrightCopilotTarget uses Playwright to interact with Microsoft Copilot web UI.

    This target handles both text and image inputs, automatically navigating the Copilot
    interface including the dropdown menu for image uploads.

    Both Consumer and M365 Copilot responses can contain text and images. When multimodal
    content is detected, the target will return multiple response pieces with appropriate
    data types.

    Parameters:
        page (Page): The Playwright page object to use for interaction.
        copilot_type (CopilotType): The type of Copilot interface (Consumer or M365).
    """

    # Constants for timeouts and retry logic
    MAX_WAIT_TIME_SECONDS: int = 300
    RESPONSE_COMPLETE_WAIT_MS: int = 3000
    POLL_INTERVAL_MS: int = 2000
    RETRY_ATTEMPTS: int = 5
    RETRY_DELAY_MS: int = 500

    # Wait time constants for image loading
    MIN_IMAGE_WAIT_SECONDS: int = 3
    MAX_IMAGE_WAIT_SECONDS: int = 15
    IMAGE_STABILITY_ITERATIONS: int = 3

    # Supported data types
    SUPPORTED_DATA_TYPES = {"text", "image_path"}

    # Placeholder text constants
    PLACEHOLDER_GENERATING_RESPONSE: str = "generating response"
    PLACEHOLDER_GENERATING: str = "generating"
    PLACEHOLDER_THINKING: str = "thinking"

    # DOM selector constants
    SELECTOR_IFRAME: str = "iframe"
    SELECTOR_IMAGE: str = "img"
    ARIA_LABEL_THUMBNAIL: str = 'button[aria-label*="Thumbnail"] img'

    # HTML attribute constants
    ATTR_SRC: str = "src"
    ATTR_ID: str = "id"

    # Image data URL prefix
    IMAGE_DATA_URL_PREFIX: str = "data:image/"

    # URL identifiers
    M365_URL_IDENTIFIER: str = "m365"

    # Login requirement message
    LOGIN_REQUIRED_HEADER: str = "Sign in for the full experience"

    def __init__(self, *, page: "Page", copilot_type: CopilotType = CopilotType.CONSUMER) -> None:
        """
        Initialize the Playwright Copilot target.

        Args:
            page (Page): The Playwright page object for browser interaction.
            copilot_type (CopilotType): The type of Copilot to interact with.
                Defaults to CopilotType.CONSUMER.

        Raises:
            RuntimeError: If the Playwright page is not initialized.
            ValueError: If the page URL doesn't match the specified copilot_type.
        """
        super().__init__()
        self._page = page
        self._type = copilot_type

        if not page:
            raise RuntimeError(
                "Playwright page is not initialized. "
                "Please pass a Page object when initializing PlaywrightCopilotTarget."
            )

        if page and self.M365_URL_IDENTIFIER in page.url and copilot_type != CopilotType.M365:
            raise ValueError("The provided page URL indicates M365 Copilot, but the type is set to consumer.")
        if page and self.M365_URL_IDENTIFIER not in page.url and copilot_type == CopilotType.M365:
            raise ValueError("The provided page URL does not indicate M365 Copilot, but the type is set to m365.")

    def _get_selectors(self) -> CopilotSelectors:
        """
        Get the appropriate selectors for the current Copilot type.

        Returns:
            CopilotSelectors: The selectors for the Copilot interface.
        """
        if self._type == CopilotType.CONSUMER:
            return CopilotSelectors(
                input_selector="#userInput",
                send_button_selector='button[data-testid="submit-button"]',
                ai_messages_selector='div[data-content="ai-message"]',
                ai_messages_group_selector=r'[data-content="ai-message"] > div > div.group\/ai-message-item',
                text_content_selector="p > span",
                plus_button_dropdown_selector='button[aria-label="Open"]',
                file_picker_selector='button[aria-label="Add images or files"]',
            )
        else:  # M365 Copilot
            return CopilotSelectors(
                input_selector='span[role="textbox"][contenteditable="true"][aria-label="Message Copilot"]',
                send_button_selector='button[type="submit"]',
                ai_messages_selector='div[data-testid="copilot-message-div"]',
                ai_messages_group_selector=(
                    'div[data-testid="copilot-message-div"] > div > div > div > div > div > div > div > div > div > div'
                ),
                text_content_selector="div > p",
                plus_button_dropdown_selector='button[aria-label="Add content"]',
                file_picker_selector='span.fui-MenuItem__content:has-text("Upload images and files")',
            )

    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Send a message to Microsoft Copilot and return the response.

        Args:
            message (Message): The message to send. Can contain multiple pieces
                of type 'text' or 'image_path'.

        Returns:
            list[Message]: A list containing the response from Copilot.

        Raises:
            RuntimeError: If an error occurs during interaction.
        """
        self._validate_request(message=message)

        try:
            response_content = await self._interact_with_copilot_async(message)
        except Exception as e:
            raise RuntimeError(f"An error occurred during interaction: {str(e)}") from e

        # For response construction, we'll use the first piece as reference
        request_piece = message.message_pieces[0]

        if isinstance(response_content, str):
            # Single text response (backward compatibility)
            response_entry = construct_response_from_request(
                request=request_piece, response_text_pieces=[response_content]
            )
        else:
            # Multimodal response with text and/or images
            response_message_pieces = []
            for piece_data, piece_type in response_content:
                response_piece = MessagePiece(
                    role="assistant",
                    original_value=piece_data,
                    conversation_id=request_piece.conversation_id,
                    labels=request_piece.labels,
                    prompt_target_identifier=request_piece.prompt_target_identifier,
                    attack_identifier=request_piece.attack_identifier,
                    original_value_data_type=piece_type,
                    converted_value_data_type=piece_type,
                    prompt_metadata=request_piece.prompt_metadata,
                    response_error="none",
                )
                response_message_pieces.append(response_piece)

            response_entry = Message(message_pieces=response_message_pieces)

        return [response_entry]

    async def _interact_with_copilot_async(self, message: Message) -> Union[str, List[Tuple[str, PromptDataType]]]:
        """
        Interact with Microsoft Copilot interface to send multimodal prompts.

        Args:
            message: The message containing text and/or image pieces to send.

        Returns:
            Union[str, List[Tuple[str, PromptDataType]]]: The response content from Copilot,
                either as a single text string or a list of (data, data_type) tuples.
        """
        selectors = self._get_selectors()

        # Handle multimodal input - process all pieces in the request
        for piece in message.message_pieces:
            if piece.converted_value_data_type == "text":
                await self._send_text_async(text=piece.converted_value, input_selector=selectors.input_selector)
            elif piece.converted_value_data_type == "image_path":
                await self._upload_image_async(piece.converted_value)

        return await self._wait_for_response_async(selectors)

    async def _wait_for_response_async(
        self, selectors: CopilotSelectors
    ) -> Union[str, List[Tuple[str, PromptDataType]]]:
        """
        Wait for Copilot's response and extract the text and/or images.

        Args:
            selectors (CopilotSelectors): The selectors for the Copilot interface.

        Returns:
            Union[str, List[Tuple[str, PromptDataType]]]: The response content from Copilot,
                either as a single text string or a list of (data, data_type) tuples.

        Raises:
            TimeoutError: If waiting for the AI response times out.
        """
        # Count current AI messages and message groups before sending
        initial_ai_messages = await self._page.eval_on_selector_all(
            selectors.ai_messages_selector, "elements => elements.length"
        )
        initial_ai_message_groups = await self._page.query_selector_all(selectors.ai_messages_group_selector)
        initial_group_count = len(initial_ai_message_groups)
        logger.debug(f"Initial message group count before sending: {initial_group_count}")

        await self._page.click(selectors.send_button_selector)

        # Wait for the next AI message to appear
        expected_ai_messages = initial_ai_messages + 1
        start_time = time.time()
        current_ai_messages = initial_ai_messages

        while time.time() - start_time < self.MAX_WAIT_TIME_SECONDS:
            current_ai_messages = await self._page.eval_on_selector_all(
                selectors.ai_messages_selector, "elements => elements.length"
            )
            if current_ai_messages >= expected_ai_messages:
                # Message appeared, but check if it has actual content (not just loading)
                logger.debug("Found new AI message, checking if content is ready...")
                # Wait a bit for content to load
                await self._page.wait_for_timeout(self.RESPONSE_COMPLETE_WAIT_MS)

                # Try to extract content to see if it's ready
                content = await self._extract_content_if_ready_async(selectors, initial_group_count)
                if content is not None:
                    return content

            elapsed = time.time() - start_time
            logger.debug(f"Still waiting for response... {elapsed:.1f}s elapsed")
            await self._page.wait_for_timeout(self.POLL_INTERVAL_MS)
        else:
            raise TimeoutError(
                f"Timed out waiting for AI response. Expected: {expected_ai_messages}, Got: {current_ai_messages}"
            )

    async def _extract_content_if_ready_async(
        self, selectors: CopilotSelectors, initial_group_count: int
    ) -> Union[str, List[Tuple[str, PromptDataType]], None]:
        """
        Extract content if ready, otherwise return None.

        Checks if the content is ready by extracting it and verifying it's not placeholder text.

        Args:
            selectors (CopilotSelectors): The selectors for the Copilot interface.
            initial_group_count (int): Number of message groups before this response.

        Returns:
            Union[str, List[Tuple[str, PromptDataType]], None]: The extracted content if ready,
                None if content is not ready yet or extraction fails.
        """
        try:
            test_content = await self._extract_multimodal_content_async(selectors, initial_group_count)
            content_ready = False

            # Check for placeholder text
            placeholder_texts = [
                self.PLACEHOLDER_GENERATING_RESPONSE,
                self.PLACEHOLDER_GENERATING,
                self.PLACEHOLDER_THINKING,
            ]

            if isinstance(test_content, str):
                text_lower = test_content.strip().lower()
                # Content is ready if it's not empty and not a placeholder
                content_ready = text_lower != "" and text_lower not in placeholder_texts
            elif isinstance(test_content, list):
                content_ready = len(test_content) > 0

            if content_ready:
                logger.debug("Content is ready!")
                return test_content
            else:
                logger.debug("Message exists but content not ready yet, continuing to wait...")
                return None
        except Exception as e:
            # Continue waiting if extraction fails
            logger.debug(f"Error checking content readiness: {e}")
            return None

    async def _extract_text_from_message_groups(self, ai_message_groups: List[Any], text_selector: str) -> List[str]:
        """
        Extract text content from message groups using the provided selector.

        Args:
            ai_message_groups: List of message group elements to extract text from
            text_selector: CSS selector for text elements within each group

        Returns:
            List of extracted text strings (may include placeholders)
        """
        all_text_parts = []

        for group_idx, msg_group in enumerate(ai_message_groups):
            text_elements = await msg_group.query_selector_all(text_selector)
            logger.debug(f"Found {len(text_elements)} text elements in group {group_idx + 1}")

            for text_elem in text_elements:
                text = await text_elem.text_content()
                if text:
                    all_text_parts.append(text.strip())

        return all_text_parts

    def _filter_placeholder_text(self, text_parts: List[str]) -> List[str]:
        """
        Filter out placeholder/loading text from extracted content.

        Args:
            text_parts: List of text strings to filter

        Returns:
            Filtered list without placeholder text
        """
        placeholder_texts = [
            self.PLACEHOLDER_GENERATING_RESPONSE,
            self.PLACEHOLDER_GENERATING,
            self.PLACEHOLDER_THINKING,
        ]
        return [text for text in text_parts if text.lower() not in placeholder_texts]

    async def _count_images_in_groups(self, message_groups: List[Any]) -> int:
        """
        Count total images in message groups (both iframes and direct).

        Args:
            message_groups: List of message group elements to search

        Returns:
            Total count of images found
        """
        image_count = 0
        for msg_group in message_groups:
            # Check iframes
            iframes = await msg_group.query_selector_all(self.SELECTOR_IFRAME)
            for iframe_element in iframes:
                try:
                    content_frame = await iframe_element.content_frame()
                    if content_frame:
                        iframe_imgs = await content_frame.query_selector_all(self.ARIA_LABEL_THUMBNAIL)
                        image_count += len(iframe_imgs)
                except Exception:
                    pass

            # Check direct images
            imgs = await msg_group.query_selector_all(self.ARIA_LABEL_THUMBNAIL)
            image_count += len(imgs)

        return image_count

    async def _wait_minimum_time(self, seconds: int) -> None:
        """
        Wait for a minimum amount of time, logging progress.

        Args:
            seconds: Number of seconds to wait
        """
        for i in range(seconds):
            await asyncio.sleep(1)
            logger.debug(f"Minimum wait: {i + 1}/{seconds} seconds")

    async def _wait_for_images_to_stabilize(
        self, selectors: CopilotSelectors, ai_message_groups: List[Any], initial_group_count: int = 0
    ) -> List[Any]:
        """
        Wait for images to appear and DOM to stabilize.

        Images may appear 1-5 seconds after text, and the DOM structure can change
        (e.g., from 3 groups to 2 groups). This method waits until either:
        1. Images are found, or
        2. The group count has been stable for 2 iterations, or
        3. Max wait time (10 seconds) is reached

        Args:
            selectors: The selectors for the Copilot interface
            ai_message_groups: Current list of message groups
            initial_group_count: Number of message groups before this response (to filter out old groups)

        Returns:
            Updated list of new message groups after waiting
        """
        logger.debug("Waiting for images to render...")
        min_wait = self.MIN_IMAGE_WAIT_SECONDS  # Always wait at least 3 seconds for images to appear
        max_wait = self.MAX_IMAGE_WAIT_SECONDS  # But don't wait more than 15 seconds total

        # Always wait minimum time first (images often take 2-5 seconds)
        await self._wait_minimum_time(min_wait)

        # Then check periodically if images have appeared
        last_stable_count = len(ai_message_groups)
        stable_iterations = 0
        images_found = False

        for i in range(max_wait - min_wait):
            await asyncio.sleep(1)
            all_groups = await self._page.query_selector_all(selectors.ai_messages_group_selector)
            new_groups = all_groups[initial_group_count:]
            current_count = len(new_groups)
            logger.debug(f"After {min_wait + i + 1}s total, new message group count: {current_count}")

            # Check for images in both iframes and direct elements
            image_count = await self._count_images_in_groups(new_groups)

            if image_count > 0:
                logger.debug(f"Found {image_count} images after {min_wait + i + 1}s!")
                images_found = True
                # Wait one more second to ensure everything is loaded
                await asyncio.sleep(1)
                break

            # Track DOM stability
            if current_count == last_stable_count:
                stable_iterations += 1
                if stable_iterations >= self.IMAGE_STABILITY_ITERATIONS:
                    logger.debug(
                        f"DOM stable for {self.IMAGE_STABILITY_ITERATIONS} iterations at {current_count} groups, "
                        "no images found"
                    )
                    break
            else:
                stable_iterations = 0
                last_stable_count = current_count

        if not images_found:
            logger.debug(f"No images found after waiting up to {max_wait}s")

        # Return latest new message groups (re-slice to exclude historical groups)
        all_groups = await self._page.query_selector_all(selectors.ai_messages_group_selector)
        return all_groups[initial_group_count:]  # type: ignore[no-any-return, unused-ignore]

    async def _extract_images_from_iframes(self, ai_message_groups: List[Any]) -> List[Any]:
        """
        Extract images from iframes within message groups.

        Args:
            ai_message_groups: List of message group elements to search

        Returns:
            List of image elements found in iframes
        """
        iframe_images = []

        for group_idx, msg_group in enumerate(ai_message_groups):
            iframes = await msg_group.query_selector_all(self.SELECTOR_IFRAME)
            logger.debug(f"Found {len(iframes)} iframes in message group {group_idx + 1}")

            for idx, iframe_element in enumerate(iframes):
                try:
                    iframe_id = await iframe_element.get_attribute(self.ATTR_ID)
                    logger.debug(f"Checking iframe {idx + 1} in group {group_idx + 1} with id: {iframe_id}")

                    content_frame = await iframe_element.content_frame()
                    if content_frame:
                        iframe_imgs = await content_frame.query_selector_all(self.ARIA_LABEL_THUMBNAIL)
                        logger.debug(
                            f"Found {len(iframe_imgs)} thumbnail images in iframe {idx + 1} of group {group_idx + 1}"
                        )
                        if iframe_imgs:
                            iframe_images.extend(iframe_imgs)
                    else:
                        logger.debug(f"Could not access content frame for iframe {idx + 1} in group {group_idx + 1}")
                except Exception as e:
                    logger.debug(f"Error accessing iframe {idx + 1} in group {group_idx + 1}: {e}")

        return iframe_images

    async def _extract_images_from_message_groups(
        self, selectors: CopilotSelectors, ai_message_groups: List[Any]
    ) -> List[Any]:
        """
        Extract images directly from message groups (fallback when no iframes).

        Args:
            selectors: The selectors for the Copilot interface
            ai_message_groups: List of message group elements to search

        Returns:
            List of image elements found
        """
        image_elements = []

        # Search in message groups
        for idx, msg_group in enumerate(ai_message_groups):
            imgs = await msg_group.query_selector_all(self.ARIA_LABEL_THUMBNAIL)
            if imgs:
                logger.debug(f"Found {len(imgs)} img elements in message group {idx + 1}")
                image_elements.extend(imgs)
            else:
                logger.debug(f"No imgs with button selector in message group {idx + 1}")

        logger.debug(f"Total {len(image_elements)} img elements found across all message groups")

        # If still no images, try broader AI message search
        if len(image_elements) == 0:
            all_ai_messages = await self._page.query_selector_all(selectors.ai_messages_selector)
            if all_ai_messages:
                # Try each AI message for images with M365 button selector
                for idx, ai_message in enumerate(all_ai_messages):
                    imgs = await ai_message.query_selector_all(self.ARIA_LABEL_THUMBNAIL)
                    if imgs:
                        logger.debug(f"Found {len(imgs)} img elements in AI message {idx + 1}")
                        image_elements.extend(imgs)

                logger.debug(f"Total {len(image_elements)} img elements found using M365 button selector")

                # Fallback to generic img selector for Consumer Copilot
                if len(image_elements) == 0:
                    for idx, ai_message in enumerate(all_ai_messages):
                        imgs = await ai_message.query_selector_all(self.SELECTOR_IMAGE)
                        if imgs:
                            logger.debug(f"Found {len(imgs)} img elements using generic selector in message {idx + 1}")
                            image_elements.extend(imgs)

        return image_elements

    async def _process_image_elements(self, image_elements: List[Any]) -> List[Tuple[str, PromptDataType]]:
        """
        Process image elements and save them to disk.

        Args:
            image_elements: List of image elements to process

        Returns:
            List of tuples containing (image_path, "image_path")
        """
        image_pieces: List[Tuple[str, PromptDataType]] = []

        for i, img_elem in enumerate(image_elements):
            src = await img_elem.get_attribute(self.ATTR_SRC)
            logger.debug(f"Image {i + 1} src: {src[:100] if src else None}...")

            if src:
                try:
                    if src.startswith(self.IMAGE_DATA_URL_PREFIX):
                        logger.debug(f"Processing data URL image {i + 1}")
                        # Extract base64 data from data URL
                        header, data = src.split(",", 1)

                        # Save the image using data serializer
                        serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
                        await serializer.save_b64_image(data=data)
                        image_path = serializer.value
                        logger.debug(f"Saved image to: {image_path}")
                        image_pieces.append((image_path, "image_path"))
                    else:
                        logger.debug(f"Image {i + 1} is not a data URL, starts with: {src[:20]}")
                except Exception as e:
                    logger.warning(f"Failed to extract image {i + 1}: {e}")
                    continue
            else:
                logger.debug(f"Image {i + 1} has no src attribute")

        return image_pieces

    async def _extract_and_filter_text_async(
        self, *, ai_message_groups: List[Any], text_selector: str
    ) -> List[Tuple[str, PromptDataType]]:
        """
        Extract and filter text content from message groups.

        Args:
            ai_message_groups: Message groups to process
            text_selector: CSS selector for text elements

        Returns:
            List of text response pieces (empty if no valid text found)
        """
        all_text_parts = await self._extract_text_from_message_groups(ai_message_groups, text_selector)
        logger.debug(f"Extracted text parts from all groups: {all_text_parts}")

        filtered_text_parts = self._filter_placeholder_text(all_text_parts)

        response_pieces: List[Tuple[str, PromptDataType]] = []
        if filtered_text_parts:
            text_content = "\n".join(filtered_text_parts).strip()
            if text_content:
                logger.debug(f"Final text content (after filtering placeholders): '{text_content}'")
                response_pieces.append((text_content, "text"))
        else:
            logger.debug("All text was placeholder text, no real content yet")

        return response_pieces

    async def _extract_all_images_async(
        self, *, selectors: CopilotSelectors, ai_message_groups: List[Any], initial_group_count: int
    ) -> List[Tuple[str, PromptDataType]]:
        """
        Extract all images from message groups using iframe and direct methods.

        Args:
            selectors: Copilot interface selectors
            ai_message_groups: Message groups to search
            initial_group_count: Initial group count for stability tracking

        Returns:
            List of image response pieces
        """
        # Wait for images to appear and DOM to stabilize
        updated_groups = await self._wait_for_images_to_stabilize(selectors, ai_message_groups, initial_group_count)
        logger.debug(f"Final new message group count for image search: {len(updated_groups)}")

        # Try to extract images from iframes first (M365 uses iframes)
        iframe_images = await self._extract_images_from_iframes(updated_groups)

        if iframe_images:
            logger.debug(f"Total {len(iframe_images)} images found in iframes within message groups!")
            image_elements = iframe_images
        else:
            logger.debug("No images found in iframes, searching message groups directly")
            image_elements = await self._extract_images_from_message_groups(selectors, updated_groups)

        # Process and save images
        return await self._process_image_elements(image_elements)

    async def _extract_fallback_text_async(self, *, ai_message_groups: List[Any]) -> str:
        """
        Extract fallback text content when no other content is found.

        Args:
            ai_message_groups: Message groups to extract from

        Returns:
            Combined text content from all groups
        """
        fallback_parts = []
        for msg_group in ai_message_groups:
            fallback_text = await msg_group.text_content()
            if fallback_text:
                fallback_parts.append(fallback_text.strip())
        fallback_result = "\n".join(fallback_parts).strip()
        logger.debug(f"Using fallback text: '{fallback_result}'")
        return fallback_result

    def _assemble_response(
        self, *, response_pieces: List[Tuple[str, PromptDataType]]
    ) -> Union[str, List[Tuple[str, PromptDataType]]]:
        """
        Assemble response pieces into appropriate return format.

        Args:
            response_pieces: List of (content, data_type) tuples

        Returns:
            Single text string for backward compatibility, or list for multimodal
        """
        if len(response_pieces) == 1 and response_pieces[0][1] == "text":
            # Single text response - maintain backward compatibility
            logger.debug(f"Returning single text response: '{response_pieces[0][0]}'")
            return response_pieces[0][0]
        elif response_pieces:
            # Multimodal or multiple pieces
            logger.debug(f"Returning {len(response_pieces)} response pieces")
            return response_pieces
        else:
            return ""

    async def _extract_multimodal_content_async(
        self, selectors: CopilotSelectors, initial_group_count: int = 0
    ) -> Union[str, List[Tuple[str, PromptDataType]]]:
        """
        Extract multimodal content (text and images) from Copilot response.

        Args:
            selectors: The selectors for the Copilot interface
            initial_group_count: Number of message groups before this response

        Returns:
            Text string or list of (content, type) tuples for multimodal responses
        """
        # Get only new message groups from this response
        all_ai_message_groups = await self._page.query_selector_all(selectors.ai_messages_group_selector)
        logger.debug(f"Found {len(all_ai_message_groups)} total AI message groups")

        ai_message_groups = all_ai_message_groups[initial_group_count:]
        logger.debug(f"Processing {len(ai_message_groups)} new message groups (skipping first {initial_group_count})")

        if not ai_message_groups:
            logger.debug("No new AI message groups found!")
            return ""

        # Extract text content
        text_pieces = await self._extract_and_filter_text_async(
            ai_message_groups=ai_message_groups, text_selector=selectors.text_content_selector
        )

        # Extract image content
        image_pieces = await self._extract_all_images_async(
            selectors=selectors, ai_message_groups=ai_message_groups, initial_group_count=initial_group_count
        )

        # Combine all response pieces
        response_pieces = text_pieces + image_pieces

        # Return appropriate format, with fallback if needed
        if response_pieces:
            return self._assemble_response(response_pieces=response_pieces)
        else:
            return await self._extract_fallback_text_async(ai_message_groups=ai_message_groups)

    async def _send_text_async(self, *, text: str, input_selector: str) -> None:
        """
        Send text input to Copilot interface.

        Args:
            text: The text to send.
            input_selector: The CSS selector for the input field.
        """
        # For M365 Copilot's contenteditable span, use type() instead of fill()
        await self._page.locator(input_selector).click()  # Focus first
        await self._page.locator(input_selector).type(text)

    async def _upload_image_async(self, image_path: str) -> None:
        """
        Handle image upload through Copilot's dropdown interface.

        Args:
            image_path: The file path of the image to upload.
        """
        selectors = self._get_selectors()

        # First, click the button to open the dropdown with retry logic
        await self._click_dropdown_button_async(selectors.plus_button_dropdown_selector)

        # Wait for dropdown to appear with the file picker button
        add_files_button = self._page.locator(selectors.file_picker_selector)
        await add_files_button.wait_for(state="visible", timeout=5000)

        # Click the button and handle the file picker
        async with self._page.expect_file_chooser() as fc_info:
            await add_files_button.click()
        file_chooser = await fc_info.value
        await file_chooser.set_files(image_path)

        # Check for login requirement in Consumer Copilot
        await self._check_login_requirement_async()

    async def _click_dropdown_button_async(self, selector: str) -> None:
        """
        Click the dropdown button with retry logic.

        Args:
            selector: The CSS selector for the dropdown button.

        Raises:
            RuntimeError: If the button cannot be found or clicked.
        """
        add_content_button = self._page.locator(selector)

        # First, wait for the button to potentially appear
        try:
            await add_content_button.wait_for(state="attached", timeout=3000)
        except Exception:
            pass  # Continue with retry logic if wait fails

        # Retry mechanism: check button count up to 5 times with 500ms delays
        button_found = False
        for attempt in range(self.RETRY_ATTEMPTS):
            button_count = await add_content_button.count()
            logger.debug(f"Attempt {attempt + 1}: Found {button_count} buttons with selector '{selector}'")

            if button_count > 0:
                # Additional checks for visibility and enabled state
                try:
                    is_visible = await add_content_button.first.is_visible()
                    is_enabled = await add_content_button.first.is_enabled()
                    logger.debug(f"Button state - Visible: {is_visible}, Enabled: {is_enabled}")

                    if is_visible and is_enabled:
                        button_found = True
                        break
                except Exception as e:
                    logger.debug(f"Error checking button state: {e}")

            await self._page.wait_for_timeout(self.RETRY_DELAY_MS)

        if not button_found:
            raise RuntimeError("Could not find button to open the dropdown for uploading an image.")

        await add_content_button.click()

    async def _check_login_requirement_async(self) -> None:
        """
        Check if login is required for Consumer Copilot features.

        Raises:
            RuntimeError: If login is required to access advanced features.
        """
        # In Consumer Copilot we can't submit pictures which will surface by prompting for login
        sign_in_header_count = await self._page.locator(f'h1:has-text("{self.LOGIN_REQUIRED_HEADER}")').count()
        sign_in_header_present = sign_in_header_count > 0
        if sign_in_header_present:
            raise RuntimeError("Login required to access advanced features in Consumer Copilot.")

    def _validate_request(self, *, message: Message) -> None:
        """
        Validate that the message is compatible with Copilot.

        Args:
            message: The message to validate.

        Raises:
            ValueError: If the message has no pieces.
            ValueError: If any piece has an unsupported data type.
        """
        if not message.message_pieces:
            raise ValueError("This target requires at least one message piece.")

        # Validate that all pieces are supported types
        for i, piece in enumerate(message.message_pieces):
            piece_type = piece.converted_value_data_type
            if piece_type not in self.SUPPORTED_DATA_TYPES:
                supported_types = ", ".join(self.SUPPORTED_DATA_TYPES)
                raise ValueError(
                    f"This target only supports {supported_types} prompt input. Piece {i} has type: {piece_type}."
                )
