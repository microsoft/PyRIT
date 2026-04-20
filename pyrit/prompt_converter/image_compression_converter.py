# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from io import BytesIO
from typing import Any, Literal, Optional
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class ImageCompressionConverter(PromptConverter):
    """
    Compresses images to reduce file size while preserving visual quality.

    This converter supports multiple compression strategies across JPEG, PNG, and WEBP formats,
    each with format-specific optimization settings. It can maintain the original image format
    or convert between formats as needed.

    When converting images with transparency (alpha channel) to JPEG format, the converter
    automatically composites the transparent areas onto a solid background color.

    Supported input types:
    File paths to any image that PIL can open (or URLs pointing to such images):
    https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#fully-supported-formats

    Supported output formats and their compression options:
        - JPEG: ``quality``, ``optimize``, ``progressive``
        - PNG: ``optimize``, ``compress_level``
        - WEBP: ``quality``, ``lossless``, ``method``

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
        quality: Optional[int] = None,
        optimize: Optional[bool] = None,
        progressive: Optional[bool] = None,
        compress_level: Optional[int] = None,
        lossless: Optional[bool] = None,
        method: Optional[int] = None,
        background_color: tuple[int, int, int] = (0, 0, 0),
        min_compression_threshold: int = 1024,
        fallback_to_original: bool = True,
    ):
        """
        Initialize the converter with specified compression settings.

        Args:
            output_format (str, optional): Output image format. If None, keeps original format (if supported).
            quality (int, optional): General quality setting for JPEG and WEBP formats (0-100).\n
                For JPEG format, it represents the image quality, on a scale from 0 (worst) to 95 (best).\n
                For WEBP format, the value ranges from 0 to 100; for lossy compression: 0-smallest file size and
                100-largest; for ``lossless``: 0-fastest/less efficient, and 100 gives the best compression.
            optimize (bool, optional): Whether to optimize the image during compression. \n
                For JPEG: makes the encoder perform an extra pass over the image to select optimal settings.\n
                For PNG: instructs the PNG writer to make the output file as small as possible.
            progressive (bool, optional): Whether to save JPEG images as progressive.
            compress_level (int, optional): ZLIB compression level (0-9): 1=fastest, 9=best, 0=none.
                Ignored if ``optimize`` is True (then it is forced to 9).
            lossless (bool, optional): Whether to use lossless compression for WEBP format.
            method (int, optional): Quality/speed trade-off for WEBP format (0=fast, 6=slower-better).
            background_color (tuple[int, int, int]): RGB color tuple for background when converting
                transparent images to JPEG. Defaults to black.
            min_compression_threshold (int): Minimum file size threshold for compression. Defaults to 1024 bytes.
            fallback_to_original (bool): Fallback to original if compression increases file size. Defaults to True.

        Raises:
            ValueError: If unsupported output format is specified, or if some of the parameters are out of range.
        """
        if quality is not None and (quality < 0 or quality > 100):
            raise ValueError("Quality must be between 0 and 100")
        self._quality = quality

        if output_format and output_format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Output format must be one of 'JPEG', 'PNG', or 'WEBP'")
        self._output_format = output_format

        if compress_level is not None and (compress_level < 0 or compress_level > 9):
            raise ValueError("Compress level must be between 0 and 9")
        self._compress_level = compress_level

        if method is not None and (method < 0 or method > 6):
            raise ValueError("Method must be between 0 and 6 for WEBP format")
        self._method = method

        if min_compression_threshold < 0:
            raise ValueError("Minimum compression threshold must be a non-negative integer")
        self._min_compression_threshold = min_compression_threshold

        if (
            not isinstance(background_color, tuple)
            or len(background_color) != 3
            or not all(isinstance(c, int) and 0 <= c <= 255 for c in background_color)
        ):
            raise ValueError("Background color must be a tuple of three integers between 0 and 255")

        self._optimize = optimize
        self._progressive = progressive
        self._lossless = lossless
        self._background_color = background_color
        self._fallback_to_original = fallback_to_original

        if output_format == "JPEG" and quality is not None and quality > 95:
            logger.warning(
                "Using quality > 95 for JPEG may result in larger files. Consider using a lower quality setting."
            )

    def _should_compress(self, original_size: int) -> bool:
        """
        Determine if image should be compressed.

        Args:
            original_size (int): The size of the original image in bytes.

        Returns:
            bool: True if the image should be compressed, False otherwise.
        """
        if original_size < self._min_compression_threshold:
            return False  # skip compression for small images
        return True

    def _compress_image(self, image: Image.Image, original_format: str, original_size: int) -> tuple[BytesIO, str]:
        """
        Compress the image with the specified settings. Returns the compressed image bytes and output format.

        Args:
            image (PIL.Image.Image): The image to be compressed.
            original_format (str): The original format of the image.
            original_size (int): The size of the original image in bytes.

        Returns:
            tuple[BytesIO, str]: A tuple containing the compressed image bytes and the output format.
        """
        original_format = original_format.upper()
        output_format = self._output_format or (
            original_format if original_format in ("JPEG", "PNG", "WEBP") else "JPEG"
        )

        logger.info(
            f"Compressing image: original format={original_format}, "
            f"output format={output_format}, original size={original_size} bytes"
        )

        # Handle images with transparency when converting to JPEG
        if output_format == "JPEG":
            if image.has_transparency_data:
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, self._background_color)
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")

        save_kwargs: dict[str, Any] = {}

        # Format-specific options for currently supported output types
        if output_format == "JPEG":
            save_kwargs = {}
            if self._quality is not None:
                save_kwargs["quality"] = self._quality
            if self._optimize:
                save_kwargs["optimize"] = True
            if self._progressive:
                save_kwargs["progressive"] = True
        elif output_format == "PNG":
            save_kwargs = {}
            if self._optimize:
                save_kwargs["optimize"] = True
            if self._compress_level is not None:
                save_kwargs["compress_level"] = self._compress_level
        elif output_format == "WEBP":
            save_kwargs = {}
            if self._quality is not None:
                save_kwargs["quality"] = self._quality
            if self._lossless:
                save_kwargs["lossless"] = True
            if self._method is not None:
                save_kwargs["method"] = self._method

        compressed_bytes = BytesIO()  # in-memory buffer
        image.save(compressed_bytes, output_format, **save_kwargs)
        return compressed_bytes, output_format

    async def _handle_original_image_fallback(
        self,
        prompt: str,
        input_type: PromptDataType,
        img_serializer: Any,
        original_img_bytes: bytes,
        original_format: str,
    ) -> ConverterResult:
        """
        Handle fallback to original image for both URL and file path inputs.

        Args:
            prompt (str): The original prompt (image path or URL).
            input_type (PromptDataType): The type of input data.
            img_serializer: The data serializer for the image.
            original_img_bytes (bytes): The original image bytes.
            original_format (str): The original image format.

        Returns:
            ConverterResult: The result containing path to the original image.
        """
        if input_type == "url":
            # We need to save the downloaded content locally and return the local path
            img_serializer.file_extension = original_format.lower()
            await img_serializer.save_data(original_img_bytes)
            return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
        return ConverterResult(output_text=prompt, output_type="image_path")

    async def _read_image_from_url(self, url: str) -> bytes:
        """
        Download data from URL and returns the content as bytes.

        Args:
            url (str): The URL to download the image from.

        Returns:
            bytes: The content of the image as bytes.

        Raises:
            RuntimeError: If there is an error during the download process.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.read()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Failed to download content from URL {url}: {str(e)}")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Convert the given prompt (image) by compressing it.

        Args:
            prompt (str): The image file path or URL pointing to the image to be compressed.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing path to the compressed image.

        Raises:
            ValueError: If the input type is not supported.
        """
        if input_type not in ("image_path", "url"):
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
        original_size = len(original_img_bytes)

        # This is to avoid unnecessary processing and potential quality loss for images that are already small
        if not self._should_compress(original_size):
            logger.warning(f"Image too small ({original_size} bytes), skipping compression")
            return await self._handle_original_image_fallback(
                prompt, input_type, img_serializer, original_img_bytes, original_format
            )

        # Compress the image and get back a BytesIO buffer containing the compressed data
        # along with the actual output format used (which may differ from input format)
        compressed_bytes, output_format = self._compress_image(original_img, original_format, original_size)
        compressed_bytes_value = compressed_bytes.getvalue()
        compressed_size = len(compressed_bytes_value)

        # Sometimes compression can actually increase file size so we check if we should fallback to the original
        if self._fallback_to_original and compressed_size >= original_size:
            logger.warning(f"Compression increased file size ({original_size} → {compressed_size}), using original")
            return await self._handle_original_image_fallback(
                prompt, input_type, img_serializer, original_img_bytes, original_format
            )

        # This ensures the saved file has the correct extension for its actual format
        # Only currently supported output formats are taken into account
        format_extensions = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
        img_serializer.file_extension = format_extensions.get(output_format, "jpeg")

        # Convert compressed bytes to base64 for storage via the serializer
        image_str = base64.b64encode(compressed_bytes_value)
        await img_serializer.save_b64_image(data=image_str.decode())

        compression_ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        logger.info(f"Image compressed: {original_size} → {compressed_size} ({compression_ratio:.1f}% reduction)")

        return ConverterResult(output_text=str(img_serializer.value), output_type="image_path")
