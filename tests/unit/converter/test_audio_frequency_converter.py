# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import asyncio
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from pyrit.converter.audio_frequency_converter import AudioFrequencyConverter


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize("shift_value", [0, 2000])
async def test_frequency_preserves_float_waveform_async(
    tmp_path: Path, dtype: type[np.floating], stereo: bool, shift_value: int
) -> None:
    """A floating-point WAV must keep its amplitude instead of truncating to silence."""
    samples = np.array([0.5, 0.25, -0.5, -0.25], dtype=dtype)
    expected = samples if shift_value == 0 else np.array([0.5, 0.0, 0.5, 0.0], dtype=dtype)
    if stereo:
        samples = np.column_stack((samples, -samples))
        expected = np.column_stack((expected, -expected))
    source = tmp_path / "float.wav"
    await asyncio.to_thread(wavfile.write, source, 8000, samples)

    result = await AudioFrequencyConverter(shift_value=shift_value).convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    assert rate == 8000
    assert output.dtype == samples.dtype
    np.testing.assert_allclose(output, expected, atol=1e-7)


async def test_convert_async_success(sqlite_instance):
    # Simulate WAV data
    sample_rate = 44100
    mock_audio_data = np.random.randint(-32768, 32767, size=(100,), dtype=np.int16)

    # Create a temporary file for the WAV file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioFrequencyConverter(shift_value=20000)

    # Call the convert_async method with the temporary WAV file path
    result = await converter.convert_async(prompt=original_wav_path)

    assert os.path.exists(result.output_text)
    assert isinstance(result.output_text, str)

    # Clean up the original WAV file after the test
    os.remove(original_wav_path)

    # Optionally, clean up any created files in result (if applicable)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_convert_async_stereo_audio(sqlite_instance):
    """Frequency shifting should support multi-channel WAV files."""
    sample_rate = 44100
    mock_audio_data = np.tile(np.array([[1000, -1000], [500, -500]], dtype=np.int16), (sample_rate // 2, 1))

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav_file:
        original_wav_path = temp_wav_file.name
        wavfile.write(original_wav_path, sample_rate, mock_audio_data)

    converter = AudioFrequencyConverter(shift_value=20000)
    result = await converter.convert_async(prompt=original_wav_path)

    out_rate, out_data = wavfile.read(result.output_text)
    assert out_rate == sample_rate
    assert out_data.shape == mock_audio_data.shape

    os.remove(original_wav_path)
    if os.path.exists(result.output_text):
        os.remove(result.output_text)


async def test_convert_async_file_not_found():
    # Create an instance of the converter
    converter = AudioFrequencyConverter(shift_value=20000)

    prompt = "non_existent_file.wav"

    # Ensure that an exception is raised when trying to convert a non-existent file
    with pytest.raises(FileNotFoundError):
        await converter.convert_async(prompt=prompt)
