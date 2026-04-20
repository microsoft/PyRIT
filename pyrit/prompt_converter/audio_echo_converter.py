# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging
from typing import Any, Literal

import numpy as np
from scipy.io import wavfile

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AudioEchoConverter(PromptConverter):
    """
    Adds an echo effect to an audio file.

    The echo is created by mixing a delayed, attenuated copy of the signal back
    into the original. The delay and decay parameters control the timing and
    loudness of the echo respectively. Sample rate, bit depth, and channel
    count are preserved.
    """

    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("audio_path",)

    #: Accepted audio formats for conversion.
    AcceptedAudioFormats = Literal["wav"]

    def __init__(
        self,
        *,
        output_format: AcceptedAudioFormats = "wav",
        delay: float = 0.3,
        decay: float = 0.5,
    ) -> None:
        """
        Initialize the converter with echo parameters.

        Args:
            output_format (str): The format of the audio file, defaults to "wav".
            delay (float): The echo delay in seconds. Must be greater than 0. Defaults to 0.3.
            decay (float): The decay factor for the echo (0.0 to 1.0).
                A value of 0.0 means no echo, 1.0 means the echo is as loud as
                the original. Must be between 0 and 1 (exclusive of both).
                Defaults to 0.5.

        Raises:
            ValueError: If delay is not positive or decay is not in (0, 1).
        """
        if delay <= 0:
            raise ValueError("delay must be greater than 0.")
        if decay <= 0 or decay >= 1:
            raise ValueError("decay must be between 0 and 1 (exclusive).")
        self._output_format = output_format
        self._delay = delay
        self._decay = decay

    def _apply_echo(self, data: np.ndarray[Any, Any], sample_rate: int) -> np.ndarray[Any, Any]:
        """
        Apply echo effect to a 1-D audio signal.

        Args:
            data: 1-D numpy array of audio samples.
            sample_rate: The sample rate of the audio.

        Returns:
            numpy array with the echo applied, same length as input.
        """
        delay_samples = int(self._delay * sample_rate)
        output = data.astype(np.float64).copy()

        # Add the delayed, decayed copy
        if delay_samples < len(data):
            output[delay_samples:] += self._decay * data[: len(data) - delay_samples].astype(np.float64)

        # Clip to the valid range for the original dtype
        if np.issubdtype(data.dtype, np.integer):
            info = np.iinfo(data.dtype)
            output = np.clip(output, info.min, info.max)

        return output

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        """
        Convert the given audio file by adding an echo effect.

        Args:
            prompt (str): File path to the audio file to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted audio file path.

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
            audio_bytes = await audio_serializer.read_data()

            # Read the audio file bytes and process the data
            bytes_io = io.BytesIO(audio_bytes)
            sample_rate, data = wavfile.read(bytes_io)
            original_dtype = data.dtype

            # Apply echo to each channel
            if data.ndim == 1:
                echo_data = self._apply_echo(data, sample_rate).astype(original_dtype)
            else:
                channels = [self._apply_echo(data[:, ch], sample_rate) for ch in range(data.shape[1])]
                echo_data = np.column_stack(channels).astype(original_dtype)

            # Write the processed data as a new WAV file
            output_bytes_io = io.BytesIO()
            wavfile.write(output_bytes_io, sample_rate, echo_data)

            # Save the converted bytes using the serializer
            converted_bytes = output_bytes_io.getvalue()
            await audio_serializer.save_data(data=converted_bytes)
            audio_serializer_file = str(audio_serializer.value)
            logger.info(
                "Echo effect (delay=%.3fs, decay=%.2f) applied to [%s], saved to [%s]",
                self._delay,
                self._decay,
                prompt,
                audio_serializer_file,
            )

        except Exception as e:
            logger.error("Failed to apply echo effect: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file, output_type=input_type)
