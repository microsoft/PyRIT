# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from io import BytesIO
from typing import Literal, Optional
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class ImageRotationConverter(PromptConverter):
    """
    Rotates an image by a given angle in degrees.

    This converter uses PIL's Image.rotate to rotate an image by a specified angle.
    Positive values rotate counter-clockwise. The image is expanded to fit the entire
    rotated content, and exposed background areas are filled with a configurable
    fill color (white by default).

    When converting images with transparency (alpha channel) to JPEG format, the converter
    automatically composites the transparent areas onto a solid background color.

    Supported input types:
    File paths to any image that PIL can open (or URLs pointing to such images):
    https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#fully-supported-formats

    Supported output formats:
    JPEG, PNG, or WEBP. If not specified, defaults to JPEG.

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
        angle: float = 90.0,
        fill_color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """
        Initialize the converter with the specified rotation angle and output format.

        Args:
            output_format (str, optional): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            angle (float): The rotation angle in degrees (counter-clockwise).
                Defaults to 90.0.
            fill_color (tuple[int, int, int]): The RGB color to fill exposed background areas
                after rotation. Defaults to (255, 255, 255) (white).

        Raises:
            ValueError: If unsupported output format is specified, or if the fill color is out of range.
        """
        if (
            not isinstance(fill_color, tuple)
            or len(fill_color) != 3
            or not all(isinstance(c, int) and 0 <= c <= 255 for c in fill_color)
        ):
            raise ValueError("Fill color must be a tuple of three integers between 0 and 255")
        self._fill_color = fill_color

        self._angle = angle

        if output_format and output_format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Output format must be one of 'JPEG', 'PNG', or 'WEBP'")
        self._output_format = output_format

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format, angle, and fill color parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "angle": self._angle,
                "fill_color": self._fill_color,
            },
        )

    def _rotate_image(self, image: Image.Image, original_format: str) -> tuple[BytesIO, str]:
        """
        Rotate the image by the specified angle. Returns the rotated image bytes and output format.

        Args:
            image (PIL.Image.Image): The image to rotate.
            original_format (str): The original format of the image.

        Returns:
            tuple[BytesIO, str]: A tuple containing the rotated image bytes and the output format.
        """
        original_format = original_format.upper()
        output_format = self._output_format or (
            original_format if original_format in ("JPEG", "PNG", "WEBP") else "JPEG"
        )

        logger.info(f"Rotating image: original format={original_format}, output format={output_format}")

        # Handle images with transparency when converting to JPEG
        if output_format == "JPEG":
            if image.has_transparency_data:
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

        rotated_image = image.rotate(self._angle, expand=True, fillcolor=self._fill_color)
        rotated_bytes = BytesIO()  # in-memory buffer
        rotated_image.save(rotated_bytes, output_format)
        return rotated_bytes, output_format

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
        Convert the given prompt (image) by rotating it by the specified angle.

        Args:
            prompt (str): The image file path or URL pointing to the image to be rotated.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the path to the rotated image.

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

        # Rotate the image and get back a BytesIO buffer containing the rotated data
        # along with the actual output format used (which may differ from input format)
        rotated_bytes, output_format = self._rotate_image(original_img, original_format)
        rotated_bytes_value = rotated_bytes.getvalue()

        # This ensures the saved file has the correct extension for its actual format
        # Only currently supported output formats are taken into account
        format_extensions = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
        img_serializer.file_extension = format_extensions.get(output_format, "jpeg")

        # Convert rotated image to base64 for storage via the serializer
        image_str = base64.b64encode(rotated_bytes_value)
        await img_serializer.save_b64_image(data=image_str.decode())

        logger.info(f"Image rotated by {self._angle} degrees")

        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
