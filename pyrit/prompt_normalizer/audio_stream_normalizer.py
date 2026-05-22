# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Normalizer for streaming audio: raw PCM in, converter-transformed PCM out."""

from __future__ import annotations

import os
import tempfile
import wave
from typing import TYPE_CHECKING

from pyrit.exceptions import (
    ComponentRole,
    execution_context,
    get_execution_context,
)

if TYPE_CHECKING:
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_normalizer.prompt_converter_configuration import (
        PromptConverterConfiguration,
    )


class AudioStreamNormalizer:
    """
    Normalizer that adapts raw PCM audio for streaming targets.

    Streaming attacks hold mid-turn PCM rather than a ``Message``; this class bridges
    raw PCM to PyRIT's file-based converter ecosystem by writing the audio to a
    temporary WAV, running converters via ``convert_tokens_async`` with
    ``input_type="audio_path"``, and reading the resulting PCM back. Subclass to
    customize bridging behavior (alternate format adaptation, parallelism, etc.).
    """

    def __init__(self, *, start_token: str = "⟪", end_token: str = "⟫") -> None:
        """Initialize with optional token delimiters passed through to converters."""
        self._start_token = start_token
        self._end_token = end_token

    async def normalize_async(
        self,
        *,
        pcm_bytes: bytes,
        sample_rate: int,
        converter_configurations: list[PromptConverterConfiguration],
    ) -> tuple[bytes, list[ComponentIdentifier]]:
        """
        Run ``converter_configurations`` against ``pcm_bytes`` via a temp WAV bridge.

        Args:
            pcm_bytes: Raw PCM16 mono audio.
            sample_rate: Sample rate in Hz.
            converter_configurations: Same shape consumed by ``PromptNormalizer.convert_values``.

        Returns:
            ``(converted_pcm, identifiers_that_ran)``.

        Raises:
            ValueError: If converter output is not mono PCM16 at ``sample_rate``.
        """
        if not converter_configurations or not pcm_bytes:
            return pcm_bytes, []

        identifiers: list[ComponentIdentifier] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            current_path = os.path.join(tmpdir, "streaming_input.wav")
            with wave.open(current_path, "wb") as wav_out:
                wav_out.setnchannels(1)
                wav_out.setsampwidth(2)
                wav_out.setframerate(sample_rate)
                wav_out.writeframes(pcm_bytes)

            for config in converter_configurations:
                if config.prompt_data_types_to_apply and "audio_path" not in config.prompt_data_types_to_apply:
                    continue

                for converter in config.converters:
                    outer_context = get_execution_context()
                    with execution_context(
                        component_role=ComponentRole.CONVERTER,
                        attack_strategy_name=outer_context.attack_strategy_name if outer_context else None,
                        attack_identifier=outer_context.attack_identifier if outer_context else None,
                        component_identifier=converter.get_identifier(),
                        objective_target_conversation_id=(
                            outer_context.objective_target_conversation_id if outer_context else None
                        ),
                    ):
                        result = await converter.convert_tokens_async(
                            prompt=current_path,
                            input_type="audio_path",
                            start_token=self._start_token,
                            end_token=self._end_token,
                        )
                    current_path = result.output_text
                    identifiers.append(converter.get_identifier())

            with wave.open(current_path, "rb") as wav_in:
                if wav_in.getnchannels() != 1 or wav_in.getsampwidth() != 2 or wav_in.getframerate() != sample_rate:
                    raise ValueError(
                        "Converter output incompatible with streaming target: "
                        f"expected mono PCM16 @ {sample_rate} Hz, got channels={wav_in.getnchannels()} "
                        f"sampwidth={wav_in.getsampwidth()} rate={wav_in.getframerate()}."
                    )
                return wav_in.readframes(wav_in.getnframes()), identifiers
