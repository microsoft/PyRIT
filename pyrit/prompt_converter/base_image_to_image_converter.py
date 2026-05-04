# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Literal
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class BaseImageToImageConverter(PromptConverter, ABC):
    """
    Abstract base class for image converters that apply a transformation to an image.

    Handles common image I/O logic: reading from file or URL, format resolution,
    JPEG transparency handling, saving to storage, and extension mapping.
    Subclasses only need to implement ``_apply_transform`` with their specific
    PIL operation.

    When converting images with transparency (alpha channel) to JPEG format, the converter
    automatically composites the transparent areas onto a white background.

    Supported input types:
    File paths to any image that PIL can open (or URLs pointing to such images):
    https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#fully-supported-formats

    Supported output formats:
    JPEG, PNG, or WEBP. If not specified, defaults to JPEG.
    """

    SUPPORTED_INPUT_TYPES = ("image_path", "url")
    SUPPORTED_OUTPUT_TYPES = ("image_path",)
    SUPPORTED_FORMATS: tuple[str, ...] = ("JPEG", "PNG", "WEBP")

    def __init__(self, *, output_format: Literal["JPEG", "PNG", "WEBP"] | None = None) -> None:
        """
        Initialize with the specified output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported), otherwise defaults to JPEG.

        Raises:
            ValueError: If unsupported output format is specified.
        """
        if output_format and output_format not in self.SUPPORTED_FORMATS:
            raise ValueError("Output format must be one of 'JPEG', 'PNG', or 'WEBP'")
        self._output_format = output_format

    @abstractmethod
    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Apply the specific image transformation.

        Subclasses must implement this method with their PIL operation.
        The image passed in is already prepared for the target format
        (e.g., converted to RGB for JPEG). The returned image must be
        saveable in the resolved output format.

        Args:
            image (PIL.Image.Image): The image to transform.

        Returns:
            PIL.Image.Image: The transformed image.
        """

    def _resolve_output_format(self, original_format: str) -> str:
        """
        Determine the output format based on the original format and configured output format.

        Args:
            original_format (str): The original format of the image.

        Returns:
            str: The resolved output format (uppercase).
        """
        original_upper = original_format.upper()
        return self._output_format or (original_upper if original_upper in self.SUPPORTED_FORMATS else "JPEG")

    def _prepare_image_for_jpeg(self, image: Image.Image) -> Image.Image:
        """
        Prepare an image for JPEG output by handling transparency and converting to RGB.

        Args:
            image (PIL.Image.Image): The image to prepare.

        Returns:
            PIL.Image.Image: The prepared RGB image.
        """
        if image.has_transparency_data:
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            return background
        return image.convert("RGB")

    def _transform_image(self, image: Image.Image, original_format: str) -> tuple[BytesIO, str]:
        """
        Resolve format, prepare the image, apply the transform, and save to a buffer.

        Args:
            image (PIL.Image.Image): The source image.
            original_format (str): The original format of the image.

        Returns:
            tuple[BytesIO, str]: The transformed image bytes and the output format.
        """
        output_format = self._resolve_output_format(original_format)

        if output_format == "JPEG":
            image = self._prepare_image_for_jpeg(image)

        transformed = self._apply_transform(image)
        buffer = BytesIO()
        transformed.save(buffer, output_format)
        return buffer, output_format

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
        Convert the given prompt (image) by applying the configured transformation.

        Args:
            prompt (str): The image file path or URL pointing to the image.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the path to the transformed image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type '{input_type}' not supported")
        if input_type == "url" and urlparse(prompt).scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL: {prompt}. Must start with 'http://' or 'https://'")

        img_serializer = data_serializer_factory(category="prompt-memory-entries", value=prompt, data_type="image_path")

        original_img_bytes = (
            await self._read_image_from_url(prompt) if input_type == "url" else await img_serializer.read_data()
        )
        original_img = Image.open(BytesIO(original_img_bytes))
        original_format = original_img.format or "JPEG"

        transformed_bytes, output_format = self._transform_image(original_img, original_format)
        img_serializer.file_extension = output_format.lower()

        image_str = base64.b64encode(transformed_bytes.getvalue())
        await img_serializer.save_b64_image(data=image_str.decode())

        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
