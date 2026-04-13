# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import os
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

from pyrit.prompt_converter.audio_white_noise_converter import AudioWhiteNoiseConverter


@pytest.mark.asyncio
async def test_white_noise_modifies_signal(sqlite_instance):
    """Output should differ from input (noise was added)."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.zeros(num_samples, dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioWhiteNoiseConverter(noise_scale=0.1)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)
    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == num_samples

    # At least some samples should now be non-zero
    assert np.any(out_data != 0)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_white_noise_preserves_shape(sqlite_instance):
    """Output should have the same number of samples and sample rate."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioWhiteNoiseConverter(noise_scale=0.02)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == num_samples

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_white_noise_stereo(sqlite_instance):
    """Converter should handle stereo (2-channel) audio correctly."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples, 2), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioWhiteNoiseConverter(noise_scale=0.05)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert out_data.shape == (num_samples, 2)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_white_noise_small_scale_stays_close(sqlite_instance):
    """With a tiny noise_scale the output should stay close to the original."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.full(num_samples, 1000, dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioWhiteNoiseConverter(noise_scale=0.001)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    # The maximum deviation should be small relative to full scale
    max_deviation = np.max(np.abs(out_data.astype(np.float64) - 1000))
    assert max_deviation < 500  # very generous bound

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_white_noise_file_not_found():
    """Non-existent file should raise FileNotFoundError."""
    converter = AudioWhiteNoiseConverter(noise_scale=0.02)
    with pytest.raises(FileNotFoundError):
        await converter.convert_async(prompt="non_existent_file.wav")


def test_white_noise_invalid_scale_zero():
    """noise_scale of 0 should raise ValueError."""
    with pytest.raises(ValueError, match="noise_scale must be between 0"):
        AudioWhiteNoiseConverter(noise_scale=0)


def test_white_noise_invalid_scale_negative():
    """Negative noise_scale should raise ValueError."""
    with pytest.raises(ValueError, match="noise_scale must be between 0"):
        AudioWhiteNoiseConverter(noise_scale=-0.1)


def test_white_noise_invalid_scale_above_one():
    """noise_scale > 1 should raise ValueError."""
    with pytest.raises(ValueError, match="noise_scale must be between 0"):
        AudioWhiteNoiseConverter(noise_scale=1.5)


@pytest.mark.asyncio
async def test_white_noise_unsupported_input_type(sqlite_instance):
    """Passing an unsupported input_type should raise ValueError."""
    converter = AudioWhiteNoiseConverter(noise_scale=0.02)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="some_file.wav", input_type="text")
