# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import math
from typing import Literal

import numpy as np
from PIL import Image

from pyrit.common.random_context import get_random_seed
from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)


class ImageNoiseConverter(BaseImageToImageConverter):
    """
    Adds random Gaussian pixel noise to an image.

    Zero-mean Gaussian noise with the configured standard deviation (on the 0-255 scale)
    is added to every color channel, and the result is clipped to the valid range. An alpha
    channel, if present, is left unchanged, and the output has the same size as the input.

    The noise is drawn from a generator seeded through PyRIT's seeded randomness, so a
    configured seed gives the same noise for the same input.
    """

    def __init__(
        self,
        *,
        output_format: Literal["JPEG", "PNG", "WEBP"] | None = None,
        standard_deviation: float = 25.0,
    ) -> None:
        """
        Initialize the converter with the specified noise level and output format.

        Args:
            output_format (Literal["JPEG", "PNG", "WEBP"] | None): Output image format.
                Must be one of 'JPEG', 'PNG', or 'WEBP'.
                If None, keeps original format (if supported).
            standard_deviation (float): Standard deviation of the Gaussian noise on the
                0-255 scale. Must be finite and greater than 0. Defaults to 25.0.

        Raises:
            ValueError: If unsupported output format is specified, or if the standard
                deviation is not finite or not greater than 0.
        """
        if not math.isfinite(standard_deviation) or standard_deviation <= 0:
            raise ValueError(f"Standard deviation must be finite and greater than 0, got {standard_deviation}")
        self._standard_deviation = standard_deviation
        super().__init__(output_format=output_format)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with output format and standard deviation parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "output_format": self._output_format,
                "standard_deviation": self._standard_deviation,
            },
        )

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        """
        Add Gaussian noise to the color channels of the image.

        Args:
            image (PIL.Image.Image): The image to add noise to.

        Returns:
            PIL.Image.Image: The noisy image, in RGB or RGBA mode.
        """
        image = self._promote_color_key_to_alpha(self._convert_to_rgb_or_rgba(image))
        pixels = np.asarray(image, dtype=np.float64)
        rng = np.random.default_rng(get_random_seed(stream="image-noise"))
        color = pixels[..., :3]
        color += rng.normal(0.0, self._standard_deviation, size=color.shape)
        np.clip(np.rint(color, out=color), 0, 255, out=color)
        return Image.fromarray(pixels.astype(np.uint8))
