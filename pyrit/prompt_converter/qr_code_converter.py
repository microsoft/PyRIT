# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

import segno

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class QRCodeConverter(PromptConverter):
    """Converts a text string to a QR code image."""

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    def __init__(
        self,
        scale: int = 3,
        border: int = 4,
        dark_color: tuple[int, int, int] = (0, 0, 0),
        light_color: tuple[int, int, int] = (255, 255, 255),
        data_dark_color: Optional[tuple[int, int, int]] = None,
        data_light_color: Optional[tuple[int, int, int]] = None,
        finder_dark_color: Optional[tuple[int, int, int]] = None,
        finder_light_color: Optional[tuple[int, int, int]] = None,
        border_color: Optional[tuple[int, int, int]] = None,
    ):
        """
        Initialize the converter with specified parameters for QR code generation.

        Args:
            scale (int, Optional): Scaling factor that determines the width/height in pixels of each
                black/white square (known as a "module") in the QR code. Defaults to 3.
            border (int, Optional): Controls how many modules thick the border should be.
                Defaults to recommended value of 4.
            dark_color (tuple, Optional): Sets color of dark modules, using RGB values.
                Defaults to black: (0, 0, 0).
            light_color (tuple, Optional): Sets color of light modules, using RGB values.
                Defaults to white: (255, 255, 255).
            data_dark_color (tuple, Optional): Sets color of dark data modules (the modules that actually
                stores the data), using RGB values. Defaults to ``dark_color``.
            data_light_color (tuple, Optional): Sets color of light data modules, using RGB values.
                Defaults to light_color.
            finder_dark_color (tuple, Optional): Sets dark module color of finder patterns (squares located in
                three corners), using RGB values. Defaults to ``dark_color``.
            finder_light_color (tuple, Optional): Sets light module color of finder patterns, using RGB values.
                Defaults to light_color.
            border_color (tuple, Optional): Sets color of border, using RGB values. Defaults to ``light_color``.
        """
        self._scale = scale
        self._border = border
        self._dark_color = dark_color
        self._light_color = light_color
        self._data_dark_color = data_dark_color or dark_color
        self._data_light_color = data_light_color or light_color
        self._finder_dark_color = finder_dark_color or dark_color
        self._finder_light_color = finder_light_color or light_color
        self._border_color = border_color or light_color
        self._img_serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to a QR code image.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing filename of the converted QR code image.

        Raises:
            ValueError: If the input type is not supported or if the prompt is empty.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        if prompt.strip() == "":
            raise ValueError("Please provide valid text value")
        # Generate random unique filename
        img_serializer_file = str(await self._img_serializer.get_data_filename())

        # Create QRCode object
        qr = segno.make_qr(prompt)
        # Save QRCode as image with specified parameters
        qr.save(
            img_serializer_file,
            scale=self._scale,
            border=self._border,
            dark=self._dark_color,
            light=self._light_color,
            data_dark=self._data_dark_color,
            data_light=self._data_light_color,
            finder_dark=self._finder_dark_color,
            finder_light=self._finder_light_color,
            quiet_zone=self._border_color,
        )
        return ConverterResult(output_text=img_serializer_file, output_type="image_path")
