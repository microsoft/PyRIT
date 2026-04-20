# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging
from typing import Literal

import numpy as np
from scipy.io import wavfile

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class AudioFrequencyConverter(PromptConverter):
    """
    Shifts the frequency of an audio file by a specified value.
    By default, it will shift it above the human hearing range (=20 kHz).
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
            shift_value (int): The value by which the frequency will be shifted, defaults to 20000 Hz.
        """
        self._output_format = output_format
        self._shift_value = shift_value

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
            audio_bytes = await audio_serializer.read_data()

            # Read the audio file bytes and process the data
            bytes_io = io.BytesIO(audio_bytes)
            sample_rate, data = wavfile.read(bytes_io)
            shifted_data = data * np.exp(1j * 2 * np.pi * self._shift_value * np.arange(len(data)) / sample_rate)

            # Convert the real part of the shifted data to int16
            shifted_data_int16 = shifted_data.real.astype(np.int16)

            # Reset buffer and write shifted data as a new WAV file
            bytes_io.seek(0)
            wavfile.write(bytes_io, sample_rate, shifted_data_int16)

            # Retrieve the WAV bytes and save them using the serializer
            converted_bytes = bytes_io.getvalue()
            await audio_serializer.save_data(data=converted_bytes)
            audio_serializer_file = str(audio_serializer.value)
            logger.info(
                "Speech synthesized for text [{}], and the audio was saved to [{}]".format(
                    prompt, audio_serializer_file
                )
            )

        except Exception as e:
            logger.error("Failed to convert prompt to audio: %s", str(e))
            raise
        return ConverterResult(output_text=audio_serializer_file, output_type=input_type)
