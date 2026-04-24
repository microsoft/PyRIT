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


class AudioVolumeConverter(PromptConverter):
    """
    Changes the volume of an audio file by scaling the amplitude.

    A volume_factor > 1.0 increases the volume (louder),
    while a volume_factor < 1.0 decreases it (quieter).
    A volume_factor of 1.0 leaves the audio unchanged.
    The converter scales all audio samples by the given factor and clips
    the result to the valid range for the original data type.
    Sample rate, bit depth, and number of channels are preserved.
    """

    SUPPORTED_INPUT_TYPES = ("audio_path",)
    SUPPORTED_OUTPUT_TYPES = ("audio_path",)

    #: Accepted audio formats for conversion.
    AcceptedAudioFormats = Literal["wav"]

    def __init__(
        self,
        *,
        output_format: AcceptedAudioFormats = "wav",
        volume_factor: float = 1.5,
    ) -> None:
        """
        Initialize the converter with the specified output format and volume factor.

        Args:
            output_format (str): The format of the audio file, defaults to "wav".
            volume_factor (float): The factor by which to scale the volume.
                Values > 1.0 increase volume, values < 1.0 decrease volume.
                Must be greater than 0. Defaults to 1.5.

        Raises:
            ValueError: If volume_factor is not positive.
        """
        if volume_factor <= 0:
            raise ValueError("volume_factor must be greater than 0.")
        self._output_format = output_format
        self._volume_factor = volume_factor

    def _apply_volume(self, data: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        """
        Scale audio samples by the volume factor and clip to the valid range.

        Args:
            data: 1-D numpy array of audio samples.

        Returns:
            numpy array with the volume adjusted, same length and dtype as input.
        """
        scaled = data.astype(np.float64) * self._volume_factor

        # Clip to the valid range for the original dtype
        if np.issubdtype(data.dtype, np.integer):
            info = np.iinfo(data.dtype)
            scaled = np.clip(scaled, info.min, info.max)

        return scaled

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "audio_path") -> ConverterResult:
        """
        Convert the given audio file by changing its volume.

        The audio samples are scaled by the volume factor. For integer audio
        formats the result is clipped to prevent overflow.

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

            # Apply volume scaling to each channel
            if data.ndim == 1:
                # Mono audio
                volume_data = self._apply_volume(data).astype(original_dtype)
            else:
                # Multi-channel audio (e.g., stereo)
                channels = [self._apply_volume(data[:, ch]) for ch in range(data.shape[1])]
                volume_data = np.column_stack(channels).astype(original_dtype)

            # Write the processed data as a new WAV file
            output_bytes_io = io.BytesIO()
            wavfile.write(output_bytes_io, sample_rate, volume_data)

            # Save the converted bytes using the serializer
            converted_bytes = output_bytes_io.getvalue()
            await audio_serializer.save_data(data=converted_bytes)
            audio_serializer_file = str(audio_serializer.value)
            logger.info(
                "Volume changed by factor %.2f for [%s], and the audio was saved to [%s]",
                self._volume_factor,
                prompt,
                audio_serializer_file,
            )

        except Exception as e:
            logger.error("Failed to convert audio volume: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file, output_type=input_type)
