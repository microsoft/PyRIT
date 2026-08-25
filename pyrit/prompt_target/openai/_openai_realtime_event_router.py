# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared OpenAI Realtime event classification and response accumulation."""

import base64
from enum import Enum, auto
from typing import Any, ClassVar


class _OpenAIRealtimeEventKind(Enum):
    """Provider event categories shared by atomic and streaming receive policies."""

    RESPONSE_DONE = auto()
    ERROR = auto()
    AUDIO_DELTA = auto()
    AUDIO_DONE = auto()
    TRANSCRIPT_DELTA = auto()
    OUTPUT_TEXT_DONE = auto()
    RESPONSE_CREATED = auto()
    OUTPUT_ITEM = auto()
    SPEECH_STARTED = auto()
    INPUT_COMMITTED = auto()
    LIFECYCLE = auto()
    OTHER = auto()


class _OpenAIRealtimeEventRouter:
    """Classify provider events and apply response deltas to caller-owned buffers."""

    _KINDS_BY_EVENT_TYPE: ClassVar[dict[str, _OpenAIRealtimeEventKind]] = {
        "response.done": _OpenAIRealtimeEventKind.RESPONSE_DONE,
        "error": _OpenAIRealtimeEventKind.ERROR,
        "response.audio.delta": _OpenAIRealtimeEventKind.AUDIO_DELTA,
        "response.output_audio.delta": _OpenAIRealtimeEventKind.AUDIO_DELTA,
        "response.audio.done": _OpenAIRealtimeEventKind.AUDIO_DONE,
        "response.output_audio.done": _OpenAIRealtimeEventKind.AUDIO_DONE,
        "response.audio_transcript.delta": _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA,
        "response.output_audio_transcript.delta": _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA,
        "response.output_text.done": _OpenAIRealtimeEventKind.OUTPUT_TEXT_DONE,
        "response.created": _OpenAIRealtimeEventKind.RESPONSE_CREATED,
        "response.output_item.added": _OpenAIRealtimeEventKind.OUTPUT_ITEM,
        "response.output_item.created": _OpenAIRealtimeEventKind.OUTPUT_ITEM,
        "input_audio_buffer.speech_started": _OpenAIRealtimeEventKind.SPEECH_STARTED,
        "input_audio_buffer.committed": _OpenAIRealtimeEventKind.INPUT_COMMITTED,
    }
    _LIFECYCLE_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset(
        {
            "session.created",
            "session.updated",
            "conversation.created",
            "conversation.item.created",
            "conversation.item.added",
            "conversation.item.done",
            "input_audio_buffer.speech_stopped",
            "conversation.item.input_audio_transcription.completed",
            "response.output_item.done",
            "response.content_part.added",
            "response.content_part.done",
            "response.audio_transcript.done",
            "response.output_audio_transcript.done",
            "response.output_text.delta",
            "rate_limits.updated",
        }
    )
    _LIFECYCLE_KINDS: ClassVar[frozenset[_OpenAIRealtimeEventKind]] = frozenset(
        {
            _OpenAIRealtimeEventKind.RESPONSE_CREATED,
            _OpenAIRealtimeEventKind.OUTPUT_ITEM,
            _OpenAIRealtimeEventKind.SPEECH_STARTED,
            _OpenAIRealtimeEventKind.INPUT_COMMITTED,
            _OpenAIRealtimeEventKind.LIFECYCLE,
        }
    )

    @classmethod
    def classify_event(cls, event_type: str) -> _OpenAIRealtimeEventKind:
        """Return the normalized category for a provider event type."""
        event_kind = cls._KINDS_BY_EVENT_TYPE.get(event_type)
        if event_kind is not None:
            return event_kind
        if event_type in cls._LIFECYCLE_EVENT_TYPES:
            return _OpenAIRealtimeEventKind.LIFECYCLE
        return _OpenAIRealtimeEventKind.OTHER

    @classmethod
    def is_lifecycle_event(cls, event_kind: _OpenAIRealtimeEventKind) -> bool:
        """Return whether atomic receiving should log the event as lifecycle-only."""
        return event_kind in cls._LIFECYCLE_KINDS

    @staticmethod
    def get_response_id(*, event: Any) -> str | None:
        """Return the response ID carried directly or by a response payload."""
        response_id = getattr(event, "response_id", None)
        if isinstance(response_id, str):
            return response_id

        response = getattr(event, "response", None)
        nested_response_id = getattr(response, "id", None)
        return nested_response_id if isinstance(nested_response_id, str) else None

    @staticmethod
    def collect_response_delta(
        *,
        event: Any,
        event_kind: _OpenAIRealtimeEventKind,
        audio_buffer: bytearray,
        transcripts: list[str],
    ) -> None:
        """Apply an audio or transcript delta to caller-owned response buffers."""
        delta = getattr(event, "delta", "")
        if not delta:
            return
        if event_kind is _OpenAIRealtimeEventKind.AUDIO_DELTA:
            audio_buffer.extend(base64.b64decode(delta))
        elif event_kind is _OpenAIRealtimeEventKind.TRANSCRIPT_DELTA:
            transcripts.append(delta)
