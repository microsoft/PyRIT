# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from io import BytesIO
from typing import Literal, Optional
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageEnhance

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class ImageColorSaturationConverter(PromptConverter):
    """
    Adjusts the color saturation level of an image.

    This converter uses PIL's ImageEnhance.Color to adjust an image's color saturation.
    A level of 0.0 produces a grayscale (black-and-white) image, 1.0 preserves the original
    colors, and values greater than 1.0 oversaturate the colors.

    When converting images with transparency (alpha channel) to JPEG format, the converter
    automatically composites the transparent areas onto a solid background color.

    Supported input types:
    File paths to any image that PIL can open (or URLs pointing to such images):
    https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#fully-supported-formats

    Supported output formats:
    JPEG, PNG, or WEBP. If not specified, defaults to PNG.

    References:
        https://pillow.readthedocs.io/en/stable/handbook/concepts.html
        https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg-saving
        https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#png-saving
        https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#webp-saving
    """

    SUPPORTED_INPUT_TYPES = ("image_path", "url")
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    def __init__(
        self,
        *,
        output_format: Optional[Literal["JPEG", "PNG", "WEBP"]] = None,
        level: float = 0.0,
    ) -> None:
        """
        Initialize the converter with the specified color saturation level and output format.

        Args:
            output_format (str, optional): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            level (float): The color saturation level.
                0.0 produces a grayscale image (black and white).
                1.0 preserves the original colors.
                Values greater than 1.0 oversaturate the colors.
                Defaults to 0.0 (grayscale image).

        Raises:
            ValueError: If unsupported output format is specified, or if level is negative.
        """
        if level < 0:
            raise ValueError(f"Level must be non-negative, got {level}")
        self._level = level

        if output_format and output_format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Output format must be one of 'JPEG', 'PNG', or 'WEBP'")
        self._output_format = output_format

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and color saturation level parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "level": self._level,
            },
        )

    def _adjust_saturation(self, image: Image.Image, original_format: str) -> tuple[BytesIO, str]:
        """
        Adjust the color saturation of the image. Returns the adjusted image bytes and output format.

        Args:
            image (PIL.Image.Image): The image to adjust.
            original_format (str): The original format of the image.

        Returns:
            tuple[BytesIO, str]: A tuple containing the adjusted image bytes and the output format.
        """
        original_format = original_format.upper()
        output_format = self._output_format or (
            original_format if original_format in ("JPEG", "PNG", "WEBP") else "JPEG"
        )

        logger.info(
            f"Adjusting image color saturation level: original format={original_format}, "
            f"output format={output_format}"
        )

        # Handle images with transparency when converting to JPEG
        if output_format == "JPEG":
            if image.has_transparency_data:
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

        adjusted_image = ImageEnhance.Color(image).enhance(self._level)
        adjusted_bytes = BytesIO()  # in-memory buffer
        adjusted_image.save(adjusted_bytes, output_format)
        return adjusted_bytes, output_format

    async def _read_image_from_url(self, url: str) -> bytes:
        """
        Download data from a URL and return the content as bytes.

        Args:
            url (str): The URL to download the image from.

        Returns:
            bytes: The content of the image as bytes.

        Raises:
            RuntimeError: If there is an error during the download process.
        """
        try:
            async with aiohttp.ClientSession() as session, session.get(url) as response:
                response.raise_for_status()
                return await response.read()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Failed to download content from URL {url}: {str(e)}") from e

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Convert the given prompt (image) by adjusting its color saturation level.

        Args:
            prompt (str): The image file path or URL pointing to the image to be adjusted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the path to the adjusted image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type '{input_type}' not supported")
        if input_type == "url" and urlparse(prompt).scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL: {prompt}. Must start with 'http://' or 'https://'")

        img_serializer = data_serializer_factory(category="prompt-memory-entries", value=prompt, data_type="image_path")

        # Read the image data into memory as bytes for processing
        original_img_bytes = (
            await self._read_image_from_url(prompt) if input_type == "url" else await img_serializer.read_data()
        )
        original_img = Image.open(BytesIO(original_img_bytes))

        original_format = original_img.format or "JPEG"  # since PIL may not always provide a format

        # Adjust the color saturation level of the image and get back a BytesIO buffer
        # containing the adjusted data along with the actual output format used (which
        # may differ from input format)
        adjusted_bytes, output_format = self._adjust_saturation(original_img, original_format)
        adjusted_bytes_value = adjusted_bytes.getvalue()

        # This ensures the saved file has the correct extension for its actual format
        # Only currently supported output formats are taken into account
        format_extensions = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
        img_serializer.file_extension = format_extensions.get(output_format, "jpeg")

        # Convert adjusted image to base64 for storage via the serializer
        image_str = base64.b64encode(adjusted_bytes_value)
        await img_serializer.save_b64_image(data=image_str.decode())

        logger.info(f"Image color saturation level adjusted to {self._level}")

        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
