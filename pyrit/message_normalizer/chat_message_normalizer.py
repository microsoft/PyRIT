# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
import os
from typing import Any, List, Union

from pyrit.common import convert_local_image_to_data_url
from pyrit.message_normalizer.message_normalizer import (
    MessageListNormalizer,
    MessageStringNormalizer,
    SystemMessageBehavior,
    apply_system_message_behavior,
)
from pyrit.models import ChatMessage, DataTypeSerializer, Message
from pyrit.models.literals import ChatMessageRole
from pyrit.models.message_piece import MessagePiece

# Supported audio formats for OpenAI input_audio
# https://platform.openai.com/docs/guides/audio
SUPPORTED_AUDIO_FORMATS = {".wav": "wav", ".mp3": "mp3"}


class ChatMessageNormalizer(MessageListNormalizer[ChatMessage], MessageStringNormalizer):
    """
    Normalizer that converts a list of Messages to a list of ChatMessages.

    This normalizer handles both single-part and multipart messages:
    - Single piece messages: content is a simple string
    - Multiple piece messages: content is a list of dicts with type/text or type/image_url

    Args:
        use_developer_role: If True, translates "system" role to "developer" role
            for compatibility with newer OpenAI models (o1, o3, gpt-4.1+).
            Defaults to False for backward compatibility.
        system_message_behavior: How to handle system messages before conversion.
            - "keep": Keep system messages as-is (default)
            - "squash": Merge system message into first user message
            - "ignore": Drop system messages entirely
    """

    def __init__(
        self,
        *,
        use_developer_role: bool = False,
        system_message_behavior: SystemMessageBehavior = "keep",
    ) -> None:
        """
        Initialize the ChatMessageNormalizer.

        Args:
            use_developer_role: If True, translates "system" role to "developer" role.
            system_message_behavior: How to handle system messages. Defaults to "keep".
        """
        self.use_developer_role = use_developer_role
        self.system_message_behavior = system_message_behavior

    async def normalize_async(self, messages: List[Message]) -> List[ChatMessage]:
        """
        Convert a list of Messages to a list of ChatMessages.

        For single-piece text messages, content is a string.
        For multi-piece or non-text messages, content is a list of content dicts.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A list of ChatMessage objects.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        # Apply system message preprocessing
        processed_messages = await apply_system_message_behavior(messages, self.system_message_behavior)

        chat_messages: List[ChatMessage] = []
        for message in processed_messages:
            pieces = message.message_pieces
            role: ChatMessageRole = pieces[0].api_role

            # Translate system -> developer for newer OpenAI models
            if self.use_developer_role and role == "system":
                role = "developer"

            # Use simple string for single text piece, otherwise use content list
            if len(pieces) == 1 and pieces[0].converted_value_data_type == "text":
                content: Union[str, List[dict[str, Any]]] = pieces[0].converted_value
            else:
                content = [await self._piece_to_content_dict_async(piece) for piece in pieces]

            chat_messages.append(ChatMessage(role=role, content=content))

        return chat_messages

    async def normalize_string_async(self, messages: List[Message]) -> str:
        """
        Convert a list of Messages to a JSON string representation.

        This serializes the list of ChatMessages to JSON format.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A JSON string representation of the ChatMessages.
        """
        chat_messages = await self.normalize_async(messages)
        return json.dumps([msg.model_dump(exclude_none=True) for msg in chat_messages], indent=2)

    async def _piece_to_content_dict_async(self, piece: MessagePiece) -> dict[str, Any]:
        """
        Convert a MessagePiece to a content dict for multipart messages.

        Supported data types:
        - text: {"type": "text", "text": "..."}
        - image_path: {"type": "image_url", "image_url": {"url": "data:..."}}
        - audio_path: {"type": "input_audio", "input_audio": {"data": "...", "format": "wav|mp3"}}
        - url: {"type": "image_url", "image_url": {"url": "https://..."}}

        Args:
            piece: The MessagePiece to convert.

        Returns:
            A dict with 'type' and content fields appropriate for the data type.

        Raises:
            ValueError: If the data type is not supported.
        """
        data_type = piece.converted_value_data_type or piece.original_value_data_type
        content = piece.converted_value

        if data_type == "text":
            return {"type": "text", "text": content}
        elif data_type == "image_path":
            # Convert local image to base64 data URL
            data_url = await convert_local_image_to_data_url(content)
            return {"type": "image_url", "image_url": {"url": data_url}}
        elif data_type == "audio_path":
            # Convert local audio to base64 for input_audio format
            return await self._convert_audio_to_input_audio(content)
        elif data_type == "url":
            # Direct URL (typically for images)
            return {"type": "image_url", "image_url": {"url": content}}
        else:
            raise ValueError(f"Data type '{data_type}' is not yet supported for chat message content.")

    async def _convert_audio_to_input_audio(self, audio_path: str) -> dict[str, Any]:
        """
        Convert a local audio file to OpenAI input_audio format.

        Args:
            audio_path: Path to the audio file.

        Returns:
            A dict with input_audio format: {"type": "input_audio", "input_audio": {"data": "...", "format": "..."}}

        Raises:
            ValueError: If the audio format is not supported.
            FileNotFoundError: If the audio file does not exist.
        """
        ext = DataTypeSerializer.get_extension(audio_path).lower()
        if ext not in SUPPORTED_AUDIO_FORMATS:
            raise ValueError(
                f"Unsupported audio format: {ext}. Supported formats are: {list(SUPPORTED_AUDIO_FORMATS.keys())}"
            )

        audio_format = SUPPORTED_AUDIO_FORMATS[ext]

        # Read and encode the audio file
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        with open(audio_path, "rb") as f:
            audio_data = base64.b64encode(f.read()).decode("utf-8")

        return {"type": "input_audio", "input_audio": {"data": audio_data, "format": audio_format}}
