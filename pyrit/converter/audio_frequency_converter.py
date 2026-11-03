# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging
from typing import Literal

import numpy as np
from scipy import signal
from scipy.io import wavfile

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentIdentifier, PromptDataType

logger = logging.getLogger(__name__)


class AudioFrequencyConverter(Converter):
    """
    Shifts the frequency of an audio file up by a specified value.
    By default, it will shift it above the human hearing range (=20 kHz).

    The shift moves the whole spectrum up without a mirrored lower sideband. When the shift is
    non-zero, the output is written at a sample rate high enough to hold the shifted spectrum, so
    it does not alias back into the audible band. Channel layout is preserved, and floating-point
    WAV input retains its sample dtype and amplitude scale.
    """

    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("audio_path",)

    #: Accepted audio formats for conversion.
    AcceptedAudioFormats = Literal["wav"]

    def __init__(
        self,
        *,
        output_format: AcceptedAudioFormats = "wav",
        shift_value: int = 20000,
    ) -> None:
        """
        Initialize the converter with the specified output format and shift value.

        Args:
            output_format (str): The format of the audio file, defaults to "wav".
            shift_value (int): The value in Hz by which the frequency will be shifted up, defaults to 20000 Hz.

        Raises:
            ValueError: If shift_value is negative.
        """
        if shift_value < 0:
            raise ValueError(f"shift_value must be zero or positive, got {shift_value}.")
        self._output_format = output_format
        self._shift_value = shift_value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with audio frequency parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "shift_value": self._shift_value,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        """
        Convert the given audio file by shifting its frequency.

        Args:
            prompt (str): File path to the audio file to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the audio file path.

        Raises:
            ValueError: If the input type is not supported.
            Exception: If there is an error during the conversion process.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        try:
            # Create serializer to read audio data
            audio_serializer = data_serializer_factory(
                category="prompt-memory-entries", data_type="audio_path", extension=self._output_format, value=prompt
            )
            audio_bytes = await audio_serializer.read_data_async()

            # Read the audio file bytes and process the data
            bytes_io = io.BytesIO(audio_bytes)
            sample_rate, data = wavfile.read(bytes_io)
            # 8-bit PCM is unsigned and centred on 128, so shift around that midpoint.
            midpoint = 128 if data.dtype == np.uint8 else 0
            output_rate, shifted_data = self._shift_up(
                samples=data.astype(np.float64) - midpoint, sample_rate=sample_rate
            )

            # Keep the input sample format so integer audio isn't wrapped. The one-way shift can raise
            # peaks above the input's, and clipping them would add distortion that lands back in the
            # audible band, so output that would not fit is scaled down just enough instead.
            if np.issubdtype(data.dtype, np.integer):
                info = np.iinfo(data.dtype)
                shifted_data = _fit_within(samples=shifted_data, upper=info.max - midpoint, lower=info.min - midpoint)
                output_data = np.clip(shifted_data + midpoint, info.min, info.max)
            else:
                output_data = shifted_data + midpoint
            output_data = output_data.astype(data.dtype)

            # Write to a fresh buffer so a shorter output cannot retain input bytes.
            output_bytes_io = io.BytesIO()
            wavfile.write(output_bytes_io, output_rate, output_data)

            # Retrieve the WAV bytes and save them using the serializer
            converted_bytes = output_bytes_io.getvalue()
            await audio_serializer.save_data_async(data=converted_bytes)
            audio_serializer_file = str(audio_serializer.value)
            logger.info(f"Speech synthesized for text [{prompt}], and the audio was saved to [{audio_serializer_file}]")

        except Exception as e:
            logger.error("Failed to convert prompt to audio: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file, output_type=input_type)

    def _shift_up(self, *, samples: np.ndarray, sample_rate: int) -> tuple[int, np.ndarray]:
        """
        Translate every frequency up by the shift value without a mirrored sideband.

        The input band reaches the input Nyquist frequency, so after the shift it reaches
        ``shift_value + sample_rate / 2``. The samples are first upsampled by the smallest integer
        factor whose rate exceeds twice that, then shifted one way through the analytic signal.

        Args:
            samples (np.ndarray): Zero-centred samples, one column per channel when multi-channel.
            sample_rate (int): The input sample rate in Hz.

        Returns:
            tuple[int, np.ndarray]: The output sample rate and the shifted samples.
        """
        if self._shift_value == 0:
            return sample_rate, samples

        factor = (2 * self._shift_value + sample_rate) // sample_rate + 1
        output_rate = sample_rate * factor
        upsampled = signal.resample_poly(samples, factor, 1, axis=0)
        phase = np.exp(2j * np.pi * self._shift_value * np.arange(upsampled.shape[0]) / output_rate)
        if upsampled.ndim > 1:
            phase = phase[:, np.newaxis]
        return output_rate, np.real(signal.hilbert(upsampled, axis=0) * phase)


def _fit_within(*, samples: np.ndarray, upper: float, lower: float) -> np.ndarray:
    """
    Scale samples down uniformly so that none exceeds the given bounds.

    Args:
        samples (np.ndarray): Zero-centred samples.
        upper (float): The largest allowed value, at least zero.
        lower (float): The smallest allowed value, at most zero.

    Returns:
        np.ndarray: The samples unchanged when they already fit, otherwise scaled by one factor.
    """
    scale = 1.0
    peak = samples.max(initial=0.0)
    trough = samples.min(initial=0.0)
    if peak > upper:
        scale = min(scale, upper / peak)
    if trough < lower:
        scale = min(scale, lower / trough)
    return samples if scale == 1.0 else samples * scale
