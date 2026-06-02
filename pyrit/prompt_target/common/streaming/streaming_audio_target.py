# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""StreamingAudioTarget capability ABC and shared types for realtime audio prompt targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.models import Message
    from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer

#: Key set in ``MessagePiece.prompt_metadata`` by streaming targets to mark turns that
#: were interrupted by barge-in. Attacks consume this to count interrupted turns
#: without reaching into target internals. Value type is ``bool``.
STREAMING_INTERRUPTED_KEY = "interrupted"


@dataclass(frozen=True)
class ServerVadConfig:
    """Server-side voice activity detection (VAD) tuning for realtime audio targets."""

    threshold: float = 0.4
    prefix_padding_ms: int = 200
    silence_duration_ms: int = 1500

    def __post_init__(self) -> None:
        """
        Validate VAD tuning values.

        Raises:
            ValueError: If any field is outside its valid range.
        """
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {self.threshold}")
        if self.prefix_padding_ms < 0:
            raise ValueError(f"prefix_padding_ms must be non-negative, got {self.prefix_padding_ms}")
        if self.silence_duration_ms < 0:
            raise ValueError(f"silence_duration_ms must be non-negative, got {self.silence_duration_ms}")


class StreamingAudioTarget(ABC):
    """Capability interface for realtime audio targets with server-VAD barge-in support."""

    @abstractmethod
    def send_streaming_prompt_async(
        self,
        *,
        audio_chunks: AsyncIterator[bytes],
        prompt_normalizer: PromptNormalizer,
        conversation_id: str | None = None,
        request_converter_configurations: list[PromptConverterConfiguration] | None = None,
        response_converter_configurations: list[PromptConverterConfiguration] | None = None,
        prepended_conversation: list[Message] | None = None,
        vad: ServerVadConfig | None = None,
    ) -> AsyncIterator[Message]:
        """
        Stream user audio; yield one ``Message`` per server-VAD-committed turn.

        Implementations must:

        - Apply request converters to each committed audio buffer via
          ``prompt_normalizer.convert_audio_async``.
        - Apply response converters and persist each yielded ``Message`` via
          ``CentralMemory``.
        - Set ``MessagePiece.prompt_metadata[STREAMING_INTERRUPTED_KEY] = True`` on
          turns that were interrupted by barge-in.
        - Use ``prepended_conversation`` for session-level instructions (e.g.
          system prompt).

        Args:
            audio_chunks (AsyncIterator[bytes]): Raw PCM audio chunks from the caller.
            prompt_normalizer (PromptNormalizer): Normalizer used to apply converters
                and persist messages.
            conversation_id (str | None): Conversation identifier; one is
                auto-generated if not provided.
            request_converter_configurations (list[PromptConverterConfiguration] | None):
                Converters applied to each committed user turn.
            response_converter_configurations (list[PromptConverterConfiguration] | None):
                Converters applied to each assistant response.
            prepended_conversation (list[Message] | None): Session-level conversation
                context. In the current contract, only a leading system-prompt
                ``Message`` is honored at the live session; prior turns are added to
                memory but not replayed to the server.
            vad (ServerVadConfig | None): Server-side VAD tuning. ``None`` uses
                target defaults.

        Yields:
            Message: One ``Message`` per VAD-committed user turn, persisted to
                ``CentralMemory``.
        """
