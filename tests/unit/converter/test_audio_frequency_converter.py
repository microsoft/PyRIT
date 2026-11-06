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

_TONE_HZ = 440
_EDGE_SAMPLES = 200


def _tone(*, sample_rate: int, frequency: float, num_samples: int, amplitude: float) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * frequency * np.arange(num_samples) / sample_rate)


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize(("shift_value", "output_rate"), [(0, 8000), (2000, 16000)])
async def test_frequency_preserves_float_waveform_async(
    tmp_path: Path, dtype: type[np.floating], stereo: bool, shift_value: int, output_rate: int
) -> None:
    """A floating-point WAV keeps its dtype and amplitude while its tone moves up by the shift."""
    samples = _tone(sample_rate=8000, frequency=_TONE_HZ, num_samples=4000, amplitude=0.5).astype(dtype)
    num_output_samples = 4000 * output_rate // 8000
    expected = _tone(
        sample_rate=output_rate, frequency=_TONE_HZ + shift_value, num_samples=num_output_samples, amplitude=0.5
    )
    if stereo:
        samples = np.column_stack((samples, -samples))
        expected = np.column_stack((expected, -expected))
    source = tmp_path / "float.wav"
    await asyncio.to_thread(wavfile.write, source, 8000, samples)

    result = await AudioFrequencyConverter(shift_value=shift_value).convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    assert rate == output_rate
    assert output.dtype == samples.dtype
    assert output.shape == expected.shape
    np.testing.assert_allclose(output[_EDGE_SAMPLES:-_EDGE_SAMPLES], expected[_EDGE_SAMPLES:-_EDGE_SAMPLES], atol=0.005)


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize(
    ("dtype", "midpoint", "amplitude"),
    [(np.int16, 0, 1 << 14), (np.int32, 0, 1 << 30), (np.uint8, 128, 64)],
)
@pytest.mark.parametrize("stereo", [False, True])
@pytest.mark.parametrize(("shift_value", "output_rate"), [(0, 8000), (2000, 16000)])
async def test_frequency_preserves_integer_waveform_async(
    *,
    tmp_path: Path,
    dtype: type[np.integer],
    midpoint: int,
    amplitude: int,
    stereo: bool,
    shift_value: int,
    output_rate: int,
) -> None:
    """Integer WAVs keep their sample format and midpoint while their tone moves up by the shift."""
    tone = _tone(sample_rate=8000, frequency=_TONE_HZ, num_samples=4000, amplitude=amplitude)
    data = np.round(midpoint + tone).astype(dtype)
    num_output_samples = 4000 * output_rate // 8000
    expected = midpoint + _tone(
        sample_rate=output_rate,
        frequency=_TONE_HZ + shift_value,
        num_samples=num_output_samples,
        amplitude=amplitude,
    )
    if stereo:
        # Mirror the second channel around the format's midpoint.
        data = np.column_stack((data, (2 * midpoint - data.astype(np.int64)).astype(dtype)))
        expected = np.column_stack((expected, 2 * midpoint - expected))
    source = tmp_path / "int.wav"
    await asyncio.to_thread(wavfile.write, source, 8000, data)

    result = await AudioFrequencyConverter(shift_value=shift_value).convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    assert rate == output_rate
    assert output.dtype == data.dtype
    assert output.shape == expected.shape
    np.testing.assert_allclose(
        output[_EDGE_SAMPLES:-_EDGE_SAMPLES], expected[_EDGE_SAMPLES:-_EDGE_SAMPLES], atol=0.01 * amplitude + 1
    )


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize(("sample_rate", "output_rate"), [(16000, 64000), (44100, 88200)])
async def test_default_shift_moves_tone_above_hearing_without_audible_peaks_async(
    tmp_path: Path, sample_rate: int, output_rate: int
) -> None:
    """A 440 Hz tone lands at 440 Hz + 20 kHz, with no mirrored or aliased peak below 20 kHz."""
    tone = _tone(sample_rate=sample_rate, frequency=_TONE_HZ, num_samples=sample_rate, amplitude=1 << 14)
    source = tmp_path / "tone.wav"
    await asyncio.to_thread(wavfile.write, source, sample_rate, tone.astype(np.int16))

    result = await AudioFrequencyConverter().convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    spectrum = np.abs(np.fft.rfft(output.astype(np.float64) * np.hanning(len(output))))
    frequencies = np.fft.rfftfreq(len(output), d=1 / rate)
    audible = (frequencies >= 20) & (frequencies < 20000)
    assert rate == output_rate
    assert frequencies[spectrum.argmax()] == pytest.approx(_TONE_HZ + 20000, abs=1)
    assert spectrum[audible].max() < 1e-3 * spectrum.max()


@pytest.mark.usefixtures("sqlite_instance")
@pytest.mark.parametrize(("dtype", "midpoint", "amplitude"), [(np.int16, 0, 32767), (np.uint8, 128, 127)])
async def test_full_scale_input_is_scaled_instead_of_clipped_async(
    tmp_path: Path, dtype: type[np.integer], midpoint: int, amplitude: int
) -> None:
    """Shifted peaks above full scale are scaled to fit, so clipping cannot add audible distortion."""
    square = amplitude * np.sign(np.sin(2 * np.pi * _TONE_HZ * np.arange(16000) / 16000))
    source = tmp_path / "square.wav"
    await asyncio.to_thread(wavfile.write, source, 16000, (midpoint + square).astype(dtype))

    result = await AudioFrequencyConverter().convert_async(prompt=str(source))
    rate, output = await asyncio.to_thread(wavfile.read, result.output_text)

    centred = output.astype(np.float64) - midpoint
    spectrum = np.abs(np.fft.rfft(centred * np.hanning(len(centred))))
    frequencies = np.fft.rfftfreq(len(centred), d=1 / rate)
    audible = (frequencies >= 20) & (frequencies < 20000)
    assert output.dtype == dtype
    assert spectrum[audible].max() < 1e-3 * spectrum.max()


def test_init_rejects_negative_shift_value() -> None:
    with pytest.raises(ValueError, match="shift_value must be zero or positive"):
        AudioFrequencyConverter(shift_value=-1)


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
    assert out_rate == 2 * sample_rate
    assert out_data.shape == (2 * len(mock_audio_data), 2)

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
