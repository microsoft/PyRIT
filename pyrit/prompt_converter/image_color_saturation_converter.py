# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from PIL import Image, ImageEnhance

from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_converter.base_image_to_image_converter import BaseImageToImageConverter

logger = logging.getLogger(__name__)


class ImageColorSaturationConverter(BaseImageToImageConverter):
    """
    Adjusts the color saturation level of an image.

    This converter uses PIL's ImageEnhance.Color to adjust an image's color saturation.
    A level of 0.0 produces a grayscale (black-and-white) image, 1.0 preserves the original
    colors, and values greater than 1.0 oversaturate the colors.

    References:
        https://pillow.readthedocs.io/en/stable/reference/ImageEnhance.html
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        level: float = 0.0,
    ) -> None:
        """
        Initialize the converter with the specified color saturation level and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
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
        super().__init__(output_format=output_format)

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

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Adjust the color saturation of the image.

        Args:
            image (PIL.Image.Image): The image to adjust.

        Returns:
            PIL.Image.Image: The adjusted image.
        """
        return ImageEnhance.Color(image).enhance(self._level)
