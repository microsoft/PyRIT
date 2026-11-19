# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import math
from typing import Literal

from PIL import Image, ImageFilter

from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)


class ImageBlurConverter(BaseImageToImageConverter):
    """
    Applies a Gaussian blur to an image.

    This converter uses PIL's ImageFilter.GaussianBlur with a configurable radius. The output
    has the same size as the input. Images with transparency are blurred with premultiplied
    alpha, so the color of fully transparent pixels does not bleed into visible edges.

    References:
        https://pillow.readthedocs.io/en/stable/reference/ImageFilter.html#PIL.ImageFilter.GaussianBlur
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        radius: float = 2.0,
    ) -> None:
        """
        Initialize the converter with the specified blur radius and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            radius (float): The Gaussian blur radius in pixels. Must be finite and greater than 0.
                Defaults to 2.0.

        Raises:
            ValueError: If unsupported output format is specified, or if the radius is not finite
                or not greater than 0.
        """
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError(f"Radius must be finite and greater than 0, got {radius}")
        self._radius = radius
        super().__init__(output_format=output_format)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and radius parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "radius": self._radius,
            },
        )

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Blur the image with the configured radius.

        Args:
            image (PIL.Image.Image): The image to blur.

        Returns:
            PIL.Image.Image: The blurred image, in RGB or RGBA mode.
        """
        image = self._convert_to_rgb_or_rgba(image)
        if image.mode == "RGB" and image.has_transparency_data:
            # An RGB image with a transparent color key stays RGB above; make its transparency an alpha channel.
            image = image.convert("RGBA")
        blur = ImageFilter.GaussianBlur(self._radius)
        if image.mode == "RGBA":
            # Blur with premultiplied alpha so hidden colors of transparent pixels stay hidden.
            return image.convert("RGBa").filter(blur).convert("RGBA")
        return image.filter(blur)
