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


class AudioWhiteNoiseConverter(PromptConverter):
    """
    Adds white noise to an audio file.

    White noise is generated and mixed into the original signal at a level
    controlled by the noise_scale parameter. The output preserves the original
    sample rate, bit depth, channel count, and number of samples.
    """

    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("audio_path",)

    #: Accepted audio formats for conversion.
    AcceptedAudioFormats = Literal["wav"]

    def __init__(
        self,
        *,
        output_format: AcceptedAudioFormats = "wav",
        noise_scale: float = 0.02,
    ) -> None:
        """
        Initialize the converter with the white noise parameters.

        Args:
            output_format (str): The format of the audio file, defaults to "wav".
            noise_scale (float): Controls the amplitude of the added noise, expressed
                as a fraction of the signal's maximum possible value. For int16 audio
                the noise amplitude will be noise_scale * 32767. Must be greater than 0
                and at most 1.0. Defaults to 0.02.

        Raises:
            ValueError: If noise_scale is not in (0, 1].
        """
        if noise_scale <= 0 or noise_scale > 1.0:
            raise ValueError("noise_scale must be between 0 (exclusive) and 1.0 (inclusive).")
        self._output_format = output_format
        self._noise_scale = noise_scale

    def _add_noise(self, data: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        """
        Add white noise to a 1-D audio signal.

        Args:
            data: 1-D numpy array of audio samples.

        Returns:
            numpy array with white noise added, same length and dtype as input.
        """
        float_data = data.astype(np.float64)

        # Determine the amplitude range based on dtype
        if np.issubdtype(data.dtype, np.integer):
            info = np.iinfo(data.dtype)
            max_val = float(info.max)
        else:
            max_val = 1.0

        noise = np.random.normal(0, self._noise_scale * max_val, size=data.shape)
        noisy = float_data + noise

        # Clip to valid range
        if np.issubdtype(data.dtype, np.integer):
            noisy = np.clip(noisy, info.min, info.max)  # type: ignore[ty:possibly-unresolved-reference]

        return np.asarray(noisy)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        """
        Convert the given audio file by adding white noise.

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

            # Apply white noise to each channel
            if data.ndim == 1:
                noisy_data = self._add_noise(data).astype(original_dtype)
            else:
                channels = [self._add_noise(data[:, ch]) for ch in range(data.shape[1])]
                noisy_data = np.column_stack(channels).astype(original_dtype)

            # Write the processed data as a new WAV file
            output_bytes_io = io.BytesIO()
            wavfile.write(output_bytes_io, sample_rate, noisy_data)

            # Save the converted bytes using the serializer
            converted_bytes = output_bytes_io.getvalue()
            await audio_serializer.save_data(data=converted_bytes)
            audio_serializer_file = str(audio_serializer.value)
            logger.info(
                "White noise (scale=%.4f) added to [%s], saved to [%s]",
                self._noise_scale,
                prompt,
                audio_serializer_file,
            )

        except Exception as e:
            logger.error("Failed to add white noise: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file, output_type=input_type)
