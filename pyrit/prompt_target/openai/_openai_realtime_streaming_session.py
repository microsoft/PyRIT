# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private session lifecycle for OpenAI Realtime streaming conversations."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.models import Message
    from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
    from pyrit.prompt_target.common.streaming import ServerVadConfig
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget


class _OpenAIRealtimeStreamingSession:
    """
    Per-conversation lifecycle owner for one OpenAI Realtime streaming exchange.

    Internal to :mod:`pyrit.prompt_target.openai`. Constructed and consumed only by
    :meth:`RealtimeTarget.send_streaming_prompt_async`; downstream code should depend on
    the ``AsyncIterator[Message]`` contract, never on this class directly.
    """

    def __init__(
        self,
        *,
        target: RealtimeTarget,
        audio_chunks: AsyncIterator[bytes],
        prompt_normalizer: PromptNormalizer,
        conversation_id: str | None = None,
        request_converter_configurations: list[PromptConverterConfiguration] | None = None,
        response_converter_configurations: list[PromptConverterConfiguration] | None = None,
        prepended_conversation: list[Message] | None = None,
        vad: ServerVadConfig | None = None,
    ) -> None:
        self._target = target
        self._audio_chunks = audio_chunks
        self._prompt_normalizer = prompt_normalizer
        self._conversation_id = conversation_id or str(uuid.uuid4())
        self._request_converter_configurations = request_converter_configurations or []
        self._response_converter_configurations = response_converter_configurations or []
        self._prepended_conversation = prepended_conversation or []
        self._vad = vad

    async def run_async(self) -> AsyncIterator[Message]:
        """
        Drive the streaming conversation; yield one ``Message`` per VAD-committed user turn.

        Raises:
            NotImplementedError: Always. Implementation lands in Phase 2 of the
                streaming refactor; the body is scaffolded here so callers can depend
                on the public contract.

        Yields:
            Message: One ``Message`` per VAD-committed user turn (Phase 2).
        """
        raise NotImplementedError("Streaming session implementation lands in Phase 2 of PR #1766")
        yield  # pragma: no cover - keeps this function an async generator
