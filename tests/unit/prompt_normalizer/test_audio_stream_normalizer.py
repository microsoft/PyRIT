# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``AudioStreamNormalizer``."""

import os
import tempfile
import wave
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_normalizer import AudioStreamNormalizer
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)


def _make_audio_converter(transformer, *, output_sample_rate=24000, identifier_name="MockAudioConverter"):
    """Mock audio converter whose convert_tokens_async runs transformer(pcm) and emits a new WAV path."""
    converter = MagicMock()
    converter.get_identifier = MagicMock(
        return_value=ComponentIdentifier(class_name=identifier_name, class_module="tests.unit.mocks"),
    )

    async def _convert(*, prompt, input_type, start_token=None, end_token=None):
        assert input_type == "audio_path"
        with wave.open(prompt, "rb") as wf_in:
            pcm = wf_in.readframes(wf_in.getnframes())
        new_pcm = transformer(pcm)
        out_dir = tempfile.mkdtemp()
        out_path = os.path.join(out_dir, "out.wav")
        with wave.open(out_path, "wb") as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(output_sample_rate)
            wf_out.writeframes(new_pcm)
        result = MagicMock()
        result.output_text = out_path
        return result

    converter.convert_tokens_async = AsyncMock(side_effect=_convert)
    return converter


async def test_normalize_async_no_configurations_returns_input():
    normalizer = AudioStreamNormalizer()
    pcm = b"\xaa" * 1024
    out, ids = await normalizer.normalize_async(pcm_bytes=pcm, sample_rate=24000, converter_configurations=[])
    assert out == pcm
    assert ids == []


async def test_normalize_async_empty_pcm_returns_input():
    normalizer = AudioStreamNormalizer()
    bump = _make_audio_converter(lambda pcm: pcm)
    out, ids = await normalizer.normalize_async(
        pcm_bytes=b"",
        sample_rate=24000,
        converter_configurations=PromptConverterConfiguration.from_converters(converters=[bump]),
    )
    assert out == b""
    assert ids == []


async def test_normalize_async_chains_converters_and_returns_identifiers():
    normalizer = AudioStreamNormalizer()
    bump_a = _make_audio_converter(lambda pcm: bytes((b + 1) & 0xFF for b in pcm))
    bump_b = _make_audio_converter(lambda pcm: bytes((b + 2) & 0xFF for b in pcm))

    out, ids = await normalizer.normalize_async(
        pcm_bytes=b"\x00\x10\x20\x30",
        sample_rate=24000,
        converter_configurations=PromptConverterConfiguration.from_converters(converters=[bump_a, bump_b]),
    )

    assert out == b"\x03\x13\x23\x33"
    assert len(ids) == 2  # one identifier per converter that ran


async def test_normalize_async_respects_data_type_filter():
    """A configuration with prompt_data_types_to_apply not including audio_path must be skipped."""
    normalizer = AudioStreamNormalizer()
    skipped = _make_audio_converter(lambda pcm: bytes((b + 9) & 0xFF for b in pcm))
    applied = _make_audio_converter(lambda pcm: bytes((b + 1) & 0xFF for b in pcm))

    configs = [
        PromptConverterConfiguration(converters=[skipped], prompt_data_types_to_apply=["text"]),
        PromptConverterConfiguration(converters=[applied], prompt_data_types_to_apply=["audio_path"]),
    ]
    out, ids = await normalizer.normalize_async(
        pcm_bytes=b"\x00\x10", sample_rate=24000, converter_configurations=configs
    )

    # Only the audio_path-applicable converter ran (+1 not +9).
    assert out == b"\x01\x11"
    assert len(ids) == 1


async def test_normalize_async_short_circuits_when_all_configs_filtered_out():
    """When every config is text-only, skip the WAV round-trip entirely."""
    normalizer = AudioStreamNormalizer()
    text_only = _make_audio_converter(lambda pcm: bytes((b + 9) & 0xFF for b in pcm))

    configs = [
        PromptConverterConfiguration(converters=[text_only], prompt_data_types_to_apply=["text"]),
    ]
    pcm = b"\x00\x10\x20\x30"
    out, ids = await normalizer.normalize_async(pcm_bytes=pcm, sample_rate=24000, converter_configurations=configs)

    assert out == pcm  # bytes unchanged
    assert ids == []
    text_only.convert_tokens_async.assert_not_awaited()


async def test_normalize_async_rejects_mismatched_sample_rate():
    """Converter output at a different sample rate must raise ValueError."""
    normalizer = AudioStreamNormalizer()
    bad = _make_audio_converter(lambda pcm: pcm, output_sample_rate=16000)
    with pytest.raises(ValueError, match="incompatible"):
        await normalizer.normalize_async(
            pcm_bytes=b"\x00" * 1024,
            sample_rate=24000,
            converter_configurations=PromptConverterConfiguration.from_converters(converters=[bad]),
        )
