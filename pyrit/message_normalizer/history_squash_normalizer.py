# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import copy
import logging
import uuid

from pyrit.message_normalizer._helpers import (
    format_message_piece_for_context,
    get_unflattenable_converter_output_types,
)
from pyrit.message_normalizer.conversation_context_normalizer import ConversationContextNormalizer
from pyrit.message_normalizer.generic_system_squash import GenericSystemSquashNormalizer
from pyrit.message_normalizer.message_normalizer import MessageListNormalizer, MessageStringNormalizer
from pyrit.models import Message, MessagePiece

logger = logging.getLogger(__name__)


class HistorySquashNormalizer(MessageListNormalizer[Message]):
    """
    Combine conversation history and the current request into one message.

    The same implementation serves two normalization scopes. Prepended-conversation
    flows create per-send overrides with a string formatter and the explicit history
    count from the caller-owned prepended-history send context. The ordinary target capability
    pipeline uses the default formatter whenever a target does not support multiple
    turns.

    The surrounding pipeline controls when the normalizer runs; this class does not
    track turn state. In both scopes, historical content becomes text while non-text
    pieces from the current request remain separate target-facing pieces.
    """

    def __init__(
        self,
        *,
        message_normalizer: MessageStringNormalizer | None = None,
        expected_history_message_count: int | None = None,
    ) -> None:
        """
        Initialize the normalizer.

        Args:
            message_normalizer (MessageStringNormalizer | None): Optional formatter
                for the combined text. When
                omitted, use the labeled conversation-history format.
            expected_history_message_count (int | None): Optional exact number of
                messages expected before the current request. The one-shot
                prepended-history scope sets this value; the ordinary capability
                pipeline does not.

        Raises:
            ValueError: If expected_history_message_count is less than one.
        """
        if expected_history_message_count is not None and expected_history_message_count < 1:
            raise ValueError("expected_history_message_count must be at least 1")
        self._message_normalizer = message_normalizer
        self._expected_history_message_count = expected_history_message_count

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Combine history and the current request into one target-facing message.

        When there is only one message it is returned unchanged. Otherwise, the
        configured formatter receives both history and current text. The combined
        text replaces the current request's text pieces, while current non-text
        pieces retain their original positions.

        Args:
            messages (list[Message]): The conversation messages to squash.

        Returns:
            list[Message]: A single-element list containing the target-facing message.

        Raises:
            ValueError: If messages is empty, the expected history count does not
                match, or converted history cannot be represented as text.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        self._validate_expected_message_count(messages=messages)
        if len(messages) == 1:
            return list(messages)

        history = messages[:-1]
        live_request = messages[-1]
        self._validate_flattenable_converter_output(messages=history)
        self._warn_on_non_text_history(messages=history)

        original_view = self._build_original_view(messages=messages)
        converted_view = (
            self._build_converted_view(messages=messages)
            if self._contains_converted_values(messages=messages)
            else None
        )
        original_text = await self._normalize_context_async(messages=original_view)
        converted_text = original_text
        if converted_view is not None:
            converted_text = await self._normalize_context_async(messages=converted_view)

        return [
            self._build_target_request(
                live_request=live_request,
                original_text=original_text,
                converted_text=converted_text,
            )
        ]

    def _validate_expected_message_count(self, *, messages: list[Message]) -> None:
        """
        Validate the optional history-boundary contract.

        Args:
            messages (list[Message]): History followed by the current request.

        Raises:
            ValueError: If the configured history count does not match the input.
        """
        if self._expected_history_message_count is None:
            return

        expected_count = self._expected_history_message_count + 1
        if len(messages) != expected_count:
            raise ValueError(
                "History squash expected "
                f"{self._expected_history_message_count} history messages and one current request, "
                f"but received {len(messages)} messages."
            )

    async def _normalize_context_async(self, *, messages: list[Message]) -> str:
        """
        Format the text portion of history and the current request.

        Args:
            messages (list[Message]): Original-view or converted-view messages to
                format.

        Returns:
            str: The combined target-facing text.
        """
        if self._message_normalizer is None:
            return self._format_default_context(messages=messages)

        messages_to_normalize = self._filter_live_non_text_pieces(messages=messages)
        if isinstance(self._message_normalizer, ConversationContextNormalizer):
            messages_to_normalize = await GenericSystemSquashNormalizer().normalize_async(messages_to_normalize)
        return await self._message_normalizer.normalize_string_async(messages_to_normalize)

    @staticmethod
    def _format_default_context(*, messages: list[Message]) -> str:
        """
        Format history with labeled roles and current text in a separate section.

        Args:
            messages (list[Message]): History followed by the current request.

        Returns:
            str: The default labeled history representation.
        """
        history_lines = [
            f"{piece.api_role.capitalize()}: {format_message_piece_for_context(piece=piece)}"
            for message in messages[:-1]
            for piece in message.message_pieces
        ]
        current_parts = [
            piece.converted_value for piece in messages[-1].message_pieces if piece.converted_value_data_type == "text"
        ]

        sections = ["[Conversation History]\n" + "\n".join(history_lines)]
        if current_parts:
            sections.append("[Current Message]\n" + "\n".join(current_parts))
        return "\n\n".join(sections)

    @staticmethod
    def _filter_live_non_text_pieces(*, messages: list[Message]) -> list[Message]:
        filtered = list(messages)
        live_message = filtered[-1]
        text_pieces = [piece for piece in live_message.message_pieces if piece.converted_value_data_type == "text"]
        if not text_pieces:
            filtered.pop()
        else:
            filtered[-1] = live_message.model_copy(update={"message_pieces": text_pieces})
        return filtered

    @staticmethod
    def _build_original_view(*, messages: list[Message]) -> list[Message]:
        original_messages = copy.deepcopy(messages)
        for message in original_messages:
            for piece in message.message_pieces:
                piece.converted_value = piece.original_value
                piece.converted_value_data_type = piece.original_value_data_type
        return original_messages

    @staticmethod
    def _build_converted_view(*, messages: list[Message]) -> list[Message]:
        converted_messages = copy.deepcopy(messages)
        for message in converted_messages:
            for piece in message.message_pieces:
                piece.original_value = piece.converted_value
                piece.original_value_data_type = piece.converted_value_data_type
        return converted_messages

    @staticmethod
    def _contains_converted_values(*, messages: list[Message]) -> bool:
        return any(
            piece.converter_identifiers
            or piece.original_value != piece.converted_value
            or piece.original_value_data_type != piece.converted_value_data_type
            for message in messages
            for piece in message.message_pieces
        )

    @staticmethod
    def _validate_flattenable_converter_output(*, messages: list[Message]) -> None:
        output_types = get_unflattenable_converter_output_types(converted_messages=messages)
        if output_types:
            raise ValueError(
                "Cannot flatten conversation history after request converters produced "
                f"non-text output types {sorted(output_types)}. Historical conversion must produce text."
            )

    @staticmethod
    def _warn_on_non_text_history(*, messages: list[Message]) -> None:
        """Warn when native non-text history becomes target-facing text."""
        flattened_types = sorted(
            {
                piece.converted_value_data_type
                for message in messages
                for piece in message.message_pieces
                if piece.converted_value_data_type != "text"
            }
        )
        if flattened_types:
            logger.warning(
                "Conversation history contains non-text pieces %s. History squashing "
                "represents them as text placeholders for the target; memory keeps the "
                "original pieces.",
                flattened_types,
            )

    @staticmethod
    def _build_target_request(
        *,
        live_request: Message,
        original_text: str,
        converted_text: str,
    ) -> Message:
        request = copy.deepcopy(live_request)
        template_piece = next(
            (piece for piece in request.message_pieces if piece.converted_value_data_type == "text"),
            request.get_piece(),
        )
        text_piece = MessagePiece(
            id=uuid.uuid4(),
            role=template_piece.role,
            original_value=original_text,
            converted_value=converted_text,
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id=template_piece.conversation_id,
            sequence=template_piece.sequence,
            prompt_metadata=dict(template_piece.prompt_metadata),
        )
        target_pieces: list[MessagePiece] = []
        text_inserted = False
        for piece in request.message_pieces:
            if piece.converted_value_data_type == "text":
                if not text_inserted:
                    target_pieces.append(text_piece)
                    text_inserted = True
                continue
            target_pieces.append(piece)
        if not text_inserted:
            target_pieces.insert(0, text_piece)
        request.message_pieces = target_pieces
        return request
