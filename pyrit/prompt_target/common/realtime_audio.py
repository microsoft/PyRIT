# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared types for realtime audio prompt targets."""

import asyncio
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerVadConfig:
    """
    Server-side voice activity detection (VAD) tuning for realtime audio targets.

    Attributes:
        threshold: VAD activation threshold (0.0 to 1.0). Defaults to 0.4.
        prefix_padding_ms: Milliseconds of pre-roll audio retained before detected speech.
            Defaults to 200.
        silence_duration_ms: Milliseconds of silence required to detect end-of-turn.
            Defaults to 1500.
    """

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


@dataclass
class RealtimeTargetResult:
    """
    Result of a Realtime API turn, containing the audio and transcripts actually delivered.

    Attributes:
        audio_bytes: Raw PCM16 audio returned by the assistant. May be partial if the
            turn was interrupted.
        transcripts: Transcript deltas captured during the turn.
    """

    audio_bytes: bytes = b""
    transcripts: list[str] = field(default_factory=list)

    def flatten_transcripts(self) -> str:
        """Return all transcript deltas concatenated into a single string."""
        return "".join(self.transcripts)


@dataclass
class _RealtimeTurnState:
    """
    Mutable per-turn state assembled by the dispatcher and read by the cancel path.

    The dispatcher routes incoming events into this object during a turn; the
    completion future is resolved by the dispatcher with a ``RealtimeTargetResult``
    snapshotted from these fields once the turn ends normally or via interruption.

    Attributes:
        completion: Future resolved with the assembled result when the turn ends.
        is_responding: True between ``response.created`` and ``response.done`` for
            the active response.
        delivered_audio: Assistant audio bytes accumulated from ``response.audio.delta``.
            Uses ``bytearray`` so deltas append in place rather than reallocating.
        delivered_transcripts: Transcript deltas accumulated from ``response.audio_transcript.delta``.
        current_item_id: Item id of the assistant response currently being streamed.
            None until ``response.output_item.added`` fires.
        last_response_id: Response id of the in-flight response. None until
            ``response.created`` fires.
        interrupted: Set True when the cancel/truncate path runs.
    """

    completion: asyncio.Future[RealtimeTargetResult]
    is_responding: bool = False
    delivered_audio: bytearray = field(default_factory=bytearray)
    delivered_transcripts: list[str] = field(default_factory=list)
    current_item_id: str | None = None
    last_response_id: str | None = None
    interrupted: bool = False
