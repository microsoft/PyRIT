# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Concrete OpenAI Realtime event dispatcher for streaming sessions."""

import base64
import logging
from typing import Any, ClassVar

from pyrit.prompt_target.common.realtime_audio import (
    CommittedEvent,
    RealtimeEventDispatcher,
    RealtimeTargetResult,
    RealtimeTurnState,
)

logger = logging.getLogger(__name__)


class _OpenAIRealtimeDispatcher(RealtimeEventDispatcher):
    """
    Concrete ``RealtimeEventDispatcher`` for the OpenAI Realtime API.

    Routes OpenAI server events into the active ``RealtimeTurnState`` and issues
    ``response.cancel`` plus ``conversation.item.truncate`` when interrupted.
    """

    #: Error code the server returns when an explicit commit finds an empty buffer
    #: (server VAD already committed the final phrase). Benign for streaming sessions.
    _COMMIT_EMPTY_ERROR_CODE: ClassVar[str] = "input_audio_buffer_commit_empty"

    async def _route_event_async(self, *, event: Any, state: RealtimeTurnState | None) -> None:
        """Route an OpenAI Realtime event to the active turn or to an input-side callback."""
        event_type = getattr(event, "type", "")

        # Capture audio_start_ms from speech_started for the next committed event.
        # The server reports it reliably here but omits it from the commit event itself.
        # Do not return — the downstream state-aware branch still needs to fire the
        # barge-in cancel when speech starts mid-response.
        if event_type == "input_audio_buffer.speech_started":
            speech_start = getattr(event, "audio_start_ms", None)
            if speech_start is not None:
                self._pending_speech_start_ms = speech_start

        # Input-side events fire callbacks regardless of whether a turn is registered.
        if event_type == "input_audio_buffer.committed":
            item_id = getattr(event, "item_id", None)
            if item_id is None:
                return
            audio_start_ms = getattr(event, "audio_start_ms", None)
            if audio_start_ms is None:
                audio_start_ms = self._pending_speech_start_ms
            self._pending_speech_start_ms = None
            self._fire_committed_callback(
                CommittedEvent(
                    item_id=item_id,
                    audio_start_ms=audio_start_ms,
                )
            )
            return

        # Remaining events are output-side and mutate per-turn state; drop if no turn.
        if state is None or state.completion.done():
            return

        if event_type == "response.created":
            state.is_responding = True
            response = getattr(event, "response", None)
            if response is not None:
                state.last_response_id = getattr(response, "id", None)
            return

        if event_type in ("response.output_item.added", "response.output_item.created"):
            item = getattr(event, "item", None)
            if item is not None:
                state.current_item_id = getattr(item, "id", None)
            return

        if event_type in ("response.audio.delta", "response.output_audio.delta"):
            delta = getattr(event, "delta", "")
            if delta:
                state.delivered_audio.extend(base64.b64decode(delta))
            return

        if event_type in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = getattr(event, "delta", "")
            if delta:
                state.delivered_transcripts.append(delta)
            return

        if event_type == "response.done":
            response = getattr(event, "response", None)
            done_response_id = getattr(response, "id", None) if response is not None else None
            if state.last_response_id is not None and done_response_id != state.last_response_id:
                # Stale event from a cancelled response; drop without resolving.
                return
            state.is_responding = False
            state.completion.set_result(
                RealtimeTargetResult(
                    audio_bytes=bytes(state.delivered_audio),
                    transcripts=list(state.delivered_transcripts),
                )
            )
            return

        if event_type == "input_audio_buffer.speech_started" and state.is_responding:
            await self._cancel_async(state=state)
            state.is_responding = False
            state.completion.set_result(
                RealtimeTargetResult(
                    audio_bytes=bytes(state.delivered_audio),
                    transcripts=list(state.delivered_transcripts),
                    interrupted=True,
                )
            )
            return

        if event_type == "error":
            error = getattr(event, "error", None)
            code = getattr(error, "code", None) if error is not None else None
            message = getattr(error, "message", "unknown") if error is not None else "unknown"
            if code == self._COMMIT_EMPTY_ERROR_CODE:
                # A forced final commit raced an already-empty buffer (server VAD committed
                # everything). Benign and unrelated to the active turn — never fail on it.
                logger.debug(f"Ignoring benign empty input-buffer commit error: {message}")
                return
            state.completion.set_exception(RuntimeError(f"Realtime API error: {message}"))
            return

    async def _cancel_async(self, *, state: RealtimeTurnState) -> None:
        """
        Truncate the in-flight response's conversation item to what was actually delivered.

        The server auto-cancels the response when it detects new speech, so we only need to
        trim the conversation history to match the audio we received.

        Marks ``state.interrupted = True`` even when the truncate call fails.
        Does not resolve ``state.completion``; the caller (``_route_event_async``) does that.

        Args:
            state (RealtimeTurnState): The turn whose response should be cancelled.
        """
        if state.current_item_id is not None:
            # PCM16 @ 24 kHz: 48 bytes per millisecond.
            audio_end_ms = len(state.delivered_audio) // 48
            try:
                await self._connection.conversation.item.truncate(
                    item_id=state.current_item_id,
                    content_index=0,
                    audio_end_ms=audio_end_ms,
                )
            except Exception as e:
                logger.warning(
                    f"conversation.item.truncate failed for item {state.current_item_id} "
                    f"(audio_end_ms={audio_end_ms}): {e}"
                )
        state.interrupted = True
