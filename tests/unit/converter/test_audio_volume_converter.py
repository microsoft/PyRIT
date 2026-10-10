# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import asyncio
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from pyrit.converter.audio_volume_converter import AudioVolumeConverter


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize(
    ("samples", "factor", "expected"),
    [
        ([128, 128, 128], 0.5, [128, 128, 128]),
        ([64, 128, 192], 0.5, [96, 128, 160]),
        ([127, 128, 129], 0.5, [128, 128, 128]),
        ([0, 128, 255], 2.0, [0, 128, 255]),
        ([0, 64, 128, 192, 255], 1.0, [0, 64, 128, 192, 255]),
    ],
)
async def test_volume_unsigned_pcm_async(
    tmp_path: Path, stereo: bool, samples: list[int], factor: float, expected: list[int]
) -> None:
    """Volume scales unsigned PCM around 128, preserving silence and clipping correctly."""
    data = np.array(samples, dtype=np.uint8)
    expected_data = np.array(expected, dtype=np.uint8)
    if stereo:
        data = np.column_stack((data, data[::-1]))
        expected_data = np.column_stack((expected_data, expected_data[::-1]))
    source = tmp_path / "unsigned.wav"
    await asyncio.to_thread(wavfile.write, source, 8000, data)

    result = await AudioVolumeConverter(volume_factor=factor).convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    assert rate == 8000
    assert output.dtype == np.uint8
    np.testing.assert_array_equal(output, expected_data)


async def test_volume_increase(sqlite_instance):
    """Increasing volume should scale sample amplitudes up."""
    sample_rate = 44100
    num_samples = 44100
    # Use moderate values so scaling up doesn't just clip everything
    mock_audio_data = np.array([1000, -1000, 500, -500] * (num_samples // 4), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioVolumeConverter(volume_factor=2.0)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)
    assert isinstance(result.output_text, str)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    # Sample count should be unchanged
    assert len(out_data) == len(mock_audio_data)
    # The first sample should be doubled
    assert out_data[0] == 2000

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_volume_decrease(sqlite_instance):
    """Decreasing volume should scale sample amplitudes down."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.array([1000, -1000, 500, -500] * (num_samples // 4), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioVolumeConverter(volume_factor=0.5)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == len(mock_audio_data)
    # The first sample should be halved
    assert out_data[0] == 500

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_volume_factor_one_preserves_audio(sqlite_instance):
    """A volume factor of 1.0 should leave the audio unchanged."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioVolumeConverter(volume_factor=1.0)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    np.testing.assert_array_equal(out_data, mock_audio_data)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_volume_clips_to_valid_range(sqlite_instance):
    """Volume increase should clip values to the int16 range."""
    sample_rate = 44100
    # Values near the int16 max/min so scaling will clip
    mock_audio_data = np.array([30000, -30000], dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioVolumeConverter(volume_factor=2.0)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    # Should be clipped to int16 max/min
    assert out_data[0] == 32767
    assert out_data[1] == -32768

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_stereo_audio(sqlite_instance):
    """Converter should handle stereo (2-channel) audio correctly."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-16000, 16000, size=(num_samples, 2), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioVolumeConverter(volume_factor=2.0)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert out_data.shape == mock_audio_data.shape

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_convert_async_file_not_found():
    """Non-existent file should raise FileNotFoundError."""
    converter = AudioVolumeConverter(volume_factor=1.5)
    prompt = "non_existent_file.wav"

    with pytest.raises(FileNotFoundError):
        await converter.convert_async(prompt=prompt)


def test_invalid_volume_factor_zero():
    """volume_factor of 0 should raise ValueError."""
    with pytest.raises(ValueError, match="volume_factor must be finite and greater than 0"):
        AudioVolumeConverter(volume_factor=0)


def test_invalid_volume_factor_negative():
    """Negative volume_factor should raise ValueError."""
    with pytest.raises(ValueError, match="volume_factor must be finite and greater than 0"):
        AudioVolumeConverter(volume_factor=-1.0)


async def test_unsupported_input_type(sqlite_instance):
    """Passing an unsupported input_type should raise ValueError."""
    converter = AudioVolumeConverter(volume_factor=1.5)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="some_file.wav", input_type="text")


@pytest.mark.parametrize("volume_factor", [float("nan"), float("inf"), float("-inf")])
def test_invalid_volume_factor_non_finite(volume_factor: float) -> None:
    """Non-finite volume factors should fail during converter construction."""
    with pytest.raises(ValueError, match="volume_factor must be finite"):
        AudioVolumeConverter(volume_factor=volume_factor)
