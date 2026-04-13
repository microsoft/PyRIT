# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import os
import tempfile

import numpy as np
import pytest
from scipy.io import wavfile

from pyrit.prompt_converter.audio_echo_converter import AudioEchoConverter


@pytest.mark.asyncio
async def test_echo_adds_delayed_signal(sqlite_instance):
    """Echo should modify samples after the delay point."""
    sample_rate = 44100
    num_samples = 44100  # 1 second
    # Use a simple impulse so the echo is easy to verify
    mock_audio_data = np.zeros(num_samples, dtype=np.int16)
    mock_audio_data[0] = 10000  # single impulse at the start

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    delay = 0.1  # 100 ms
    decay = 0.5
    converter = AudioEchoConverter(delay=delay, decay=decay)
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)
    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == num_samples

    # The echo of the impulse should appear at the delay offset
    delay_samples = int(delay * sample_rate)
    assert out_data[delay_samples] == int(decay * 10000)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_echo_preserves_sample_count(sqlite_instance):
    """Output should have the same number of samples as input."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples,), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioEchoConverter(delay=0.2, decay=0.4)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert len(out_data) == num_samples

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_echo_stereo(sqlite_instance):
    """Converter should handle stereo (2-channel) audio correctly."""
    sample_rate = 44100
    num_samples = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(num_samples, 2), dtype=np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        original_wav_path = f.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioEchoConverter(delay=0.2, decay=0.3)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert out_data.shape == (num_samples, 2)

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


@pytest.mark.asyncio
async def test_echo_file_not_found():
    """Non-existent file should raise FileNotFoundError."""
    converter = AudioEchoConverter(delay=0.3, decay=0.5)
    with pytest.raises(FileNotFoundError):
        await converter.convert_async(prompt="non_existent_file.wav")


def test_echo_invalid_delay_zero():
    """delay of 0 should raise ValueError."""
    with pytest.raises(ValueError, match="delay must be greater than 0"):
        AudioEchoConverter(delay=0, decay=0.5)


def test_echo_invalid_delay_negative():
    """Negative delay should raise ValueError."""
    with pytest.raises(ValueError, match="delay must be greater than 0"):
        AudioEchoConverter(delay=-0.5, decay=0.5)


def test_echo_invalid_decay_zero():
    """decay of 0 should raise ValueError."""
    with pytest.raises(ValueError, match="decay must be between 0 and 1"):
        AudioEchoConverter(delay=0.3, decay=0)


def test_echo_invalid_decay_one():
    """decay of 1 should raise ValueError."""
    with pytest.raises(ValueError, match="decay must be between 0 and 1"):
        AudioEchoConverter(delay=0.3, decay=1.0)


def test_echo_invalid_decay_above_one():
    """decay > 1 should raise ValueError."""
    with pytest.raises(ValueError, match="decay must be between 0 and 1"):
        AudioEchoConverter(delay=0.3, decay=1.5)


@pytest.mark.asyncio
async def test_echo_unsupported_input_type(sqlite_instance):
    """Passing an unsupported input_type should raise ValueError."""
    converter = AudioEchoConverter(delay=0.3, decay=0.5)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="some_file.wav", input_type="text")
