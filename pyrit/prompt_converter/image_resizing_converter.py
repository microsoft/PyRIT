# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from PIL import Image

from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_converter.base_image_to_image_converter import BaseImageToImageConverter

logger = logging.getLogger(__name__)


class ImageResizingConverter(BaseImageToImageConverter):
    """
    Resizes an image by a given scale factor.

    This converter uses PIL's Image.resize to scale an image by a specified factor.
    A scale_factor of 1.0 preserves the original size, values less than 1.0 shrink
    the image, and values greater than 1.0 enlarge it.

    References:
        https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.resize
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        scale_factor: float = 0.5,
    ) -> None:
        """
        Initialize the converter with the specified scale factor and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            scale_factor (float): The factor by which to scale the image dimensions.
                1.0 preserves the original size.
                Values less than 1.0 shrink the image.
                Values greater than 1.0 enlarge the image.
                Defaults to 0.5 (halve the image dimensions).

        Raises:
            ValueError: If unsupported output format is specified, or if scale factor is not positive.
        """
        if scale_factor <= 0:
            raise ValueError(f"Scale factor must be positive, got {scale_factor}")
        self._scale_factor = scale_factor
        super().__init__(output_format=output_format)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and scale factor parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "scale_factor": self._scale_factor,
            },
        )

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Resize the image by the configured scale factor.

        Args:
            image (PIL.Image.Image): The image to resize.

        Returns:
            PIL.Image.Image: The resized image.
        """
        new_width = int(image.width * self._scale_factor)
        new_height = int(image.height * self._scale_factor)
        return image.resize((new_width, new_height), Image.Resampling.LANCZOS)
