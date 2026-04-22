# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import os
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

from pyrit.prompt_converter.audio_speed_converter import AudioSpeedConverter


@pytest.mark.asyncio
async def test_speed_up_audio(sqlite_instance):
    """Speeding up should produce fewer samples than the original."""
    sample_rate = 44100
    num_samples = 44100  # 1 second of audio
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioSpeedConverter(speed_factor=2.0)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)
    assert isinstance(result.output_text, str)

    # Read back and verify the output has fewer samples (sped up)
    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == int(num_samples / 2.0)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_slow_down_audio(sqlite_instance):
    """Slowing down should produce more samples than the original."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioSpeedConverter(speed_factor=0.5)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == int(num_samples / 0.5)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_speed_factor_one_preserves_length(sqlite_instance):
    """A speed factor of 1.0 should keep the same number of samples."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioSpeedConverter(speed_factor=1.0)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == num_samples

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_stereo_audio(sqlite_instance):
    """Converter should handle stereo (2-channel) audio correctly."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples, 2), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioSpeedConverter(speed_factor=2.0)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert out_data.shape == (int(num_samples / 2.0), 2)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_convert_async_file_not_found():
    """Non-existent file should raise FileNotFoundError."""
    converter = AudioSpeedConverter(speed_factor=1.5)
    prompt = "non_existent_file.wav"

    with pytest.raises(FileNotFoundError):
        await converter.convert_async(prompt=prompt)


def test_invalid_speed_factor_zero():
    """speed_factor of 0 should raise ValueError."""
    with pytest.raises(ValueError, match="speed_factor must be greater than 0"):
        AudioSpeedConverter(speed_factor=0)


def test_invalid_speed_factor_negative():
    """Negative speed_factor should raise ValueError."""
    with pytest.raises(ValueError, match="speed_factor must be greater than 0"):
        AudioSpeedConverter(speed_factor=-1.0)


@pytest.mark.asyncio
async def test_unsupported_input_type(sqlite_instance):
    """Passing an unsupported input_type should raise ValueError."""
    converter = AudioSpeedConverter(speed_factor=1.5)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="some_file.wav", input_type="text")
