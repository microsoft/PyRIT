# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared types for realtime audio prompt targets."""

from dataclasses import dataclass


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
