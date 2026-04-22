# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from PIL import Image

from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_converter.base_image_to_image_converter import BaseImageToImageConverter

logger = logging.getLogger(__name__)


class ImageRotationConverter(BaseImageToImageConverter):
    """
    Rotates an image by a given angle in degrees.

    This converter uses PIL's Image.rotate to rotate an image by a specified angle.
    Positive values rotate counter-clockwise. The image is expanded to fit the entire
    rotated content, and exposed background areas are filled with a configurable
    fill color (white by default).

    References:
        https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.rotate
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        angle: float = 90.0,
        fill_color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """
        Initialize the converter with the specified rotation angle and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
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
        super().__init__(output_format=output_format)

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

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Rotate the image by the configured angle.

        Args:
            image (PIL.Image.Image): The image to rotate.

        Returns:
            PIL.Image.Image: The rotated image.
        """
        return image.rotate(self._angle, expand=True, fillcolor=self._fill_color)
